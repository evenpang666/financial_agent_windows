#!/usr/bin/env python
"""Trading-day 09:00 report scheduler with local archive and optional webhook push."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "daily-push.json"
STATE_FILE = PROJECT_ROOT / "data" / "daily-push-state.json"
REPORT_DIR = PROJECT_ROOT / "data" / "reports"
CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def log(message: str):
    print(f"[{datetime.now(CHINA_TZ):%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def load_config(path: Path) -> dict:
    defaults = {
        "enabled": True,
        "data_service_url": "http://127.0.0.1:8765",
        "scheduled_time": "08:55",
        "candidate_limit": 5,
        "webhook_type": "generic",
        "webhook_url": "",
    }
    if path.exists():
        supplied = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise ValueError("推送配置必须是 JSON 对象。")
        defaults.update(supplied)
    env_url = os.getenv("FINANCE_AGENT_WEBHOOK_URL", "").strip()
    if env_url:
        defaults["webhook_url"] = env_url
    return defaults


def get_json(url: str, timeout: int = 120) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "dsh-finance-agent/0.2"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def webhook_payload(kind: str, markdown: str) -> dict:
    kind = kind.lower()
    if kind == "feishu":
        return {"msg_type": "text", "content": {"text": markdown}}
    if kind == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": markdown}}
    if kind == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": "A股开盘前研究简报", "text": markdown}}
    return {"text": markdown, "content": markdown, "type": "a_share_daily_research"}


def post_webhook(url: str, kind: str, markdown: str):
    body = json.dumps(webhook_payload(kind, markdown), ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, method="POST", headers={"Content-Type": "application/json; charset=utf-8"})
    with urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"Webhook 返回 HTTP {response.status}")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(payload: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def archive_report(day: str, markdown: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"{day}.md"
    target.write_text(markdown, encoding="utf-8")
    return target


def run_once(config: dict, force: bool = False) -> bool:
    if not config.get("enabled", True):
        log("推送已在配置中禁用。")
        return False
    today = datetime.now(CHINA_TZ).date().isoformat()
    state = load_state()
    if not force and state.get("last_success_date") == today:
        log("今日简报已成功生成，跳过重复推送。")
        return False
    base_url = str(config["data_service_url"]).rstrip("/")
    query = urlencode({"candidate_limit": int(config.get("candidate_limit", 5))})
    report = get_json(f"{base_url}/v1/daily-report?{query}")
    if not report.get("is_trading_day"):
        log("今日不是交易日，不推送开盘前简报。")
        return False
    markdown = str(report.get("markdown", "")).strip()
    if not markdown:
        raise RuntimeError("数据服务未返回简报正文。")
    report_path = archive_report(today, markdown)
    webhook_url = str(config.get("webhook_url", "")).strip()
    if webhook_url:
        post_webhook(webhook_url, str(config.get("webhook_type", "generic")), markdown)
        destination = "环境变量 Webhook" if os.getenv("FINANCE_AGENT_WEBHOOK_URL", "").strip() else str(config.get("webhook_type", "generic")) + " Webhook"
        log(f"简报已推送至{destination}。")
    else:
        log("未配置 Webhook；简报仅归档到本地。")
    save_state({"last_success_date": today, "report_path": str(report_path), "pushed": bool(webhook_url)})
    log(f"简报已生成：{report_path}")
    return True


def seconds_until_schedule(hhmm: str) -> float:
    try:
        hour, minute = (int(part) for part in hhmm.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("scheduled_time 必须使用 HH:MM 格式。")
    now = datetime.now(CHINA_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def run_daemon(config_path: Path):
    log(f"每日推送服务已启动，配置：{config_path}")
    while True:
        try:
            config = load_config(config_path)
            delay = seconds_until_schedule(str(config.get("scheduled_time", "08:55")))
            log(f"下一次检查将在约 {delay / 3600:.1f} 小时后执行。")
            time.sleep(delay)
            # Reload so webhook and candidate count changes do not require a restart.
            for attempt in range(1, 4):
                try:
                    run_once(load_config(config_path))
                    break
                except (HTTPError, URLError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                    if attempt == 3:
                        raise
                    log(f"第 {attempt} 次执行失败：{exc}；10分钟后重试。")
                    time.sleep(600)
        except KeyboardInterrupt:
            log("每日推送服务已停止。")
            return
        except (HTTPError, URLError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            log(f"连续执行失败：{exc}；60秒后重新计算下一次计划。")
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="A股交易日开盘前研究简报推送")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--once", action="store_true", help="立即检查交易日并执行一次")
    parser.add_argument("--force", action="store_true", help="忽略当天已成功状态，重新执行")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.once:
            run_once(config, force=args.force)
        else:
            run_daemon(args.config)
    except Exception as exc:
        log(f"错误：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
