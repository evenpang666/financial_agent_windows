#!/usr/bin/env python
"""Read-only local A-share data adapter for dsh-finance-agent.

Run on Windows: .\\.venv\\Scripts\\python.exe .\\scripts\\stock_data_server.py
The server intentionally binds only to 127.0.0.1 and contains no broker API.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

try:
    import akshare as ak
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency. Activate .venv and run: pip install -r requirements.txt") from exc


HOST, PORT = "127.0.0.1", 8765
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_FILE = PROJECT_ROOT / "data" / "portfolio.json"


def to_number(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return value


def iso_date(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def clean_record(record):
    return {str(key): to_number(value) for key, value in record.items()}


def normalize_holding(item: dict) -> dict:
    if not isinstance(item, dict):
        raise ValueError("每条持仓必须是对象。")
    symbol = str(item.get("symbol", "")).strip()
    if not symbol.isdigit() or len(symbol) != 6:
        raise ValueError("持仓代码必须是 6 位 A 股代码。")
    holding = {"symbol": symbol}
    name = str(item.get("name", "")).strip()
    if name:
        holding["name"] = name[:80]
    for field in ("cost_basis", "shares"):
        if field not in item or item[field] in (None, ""):
            continue
        value = float(item[field])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{field} 必须是非负数。")
        holding[field] = value
    note = str(item.get("note", "")).strip()
    if note:
        holding["note"] = note[:500]
    return holding


def portfolio_response():
    if not PORTFOLIO_FILE.exists():
        return {
            "source": "local portfolio file", "as_of": str(date.today()), "holdings": [],
            "note": "尚未保存持仓；可调用 set_portfolio 仅保存代码、成本和数量等研究字段。",
        }
    try:
        payload = json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
        holdings = [normalize_holding(item) for item in payload.get("holdings", [])]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"无法读取本地持仓文件：{exc}") from exc
    return {"source": "local portfolio file", "as_of": str(date.today()), "holdings": holdings}


def save_portfolio(payload: dict):
    if not isinstance(payload, dict) or not isinstance(payload.get("holdings"), list):
        raise ValueError("请求必须包含 holdings 数组。")
    if len(payload["holdings"]) > 100:
        raise ValueError("持仓数量不能超过 100。")
    holdings = [normalize_holding(item) for item in payload["holdings"]]
    symbols = [item["symbol"] for item in holdings]
    if len(symbols) != len(set(symbols)):
        raise ValueError("持仓代码不能重复。")
    PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_FILE.write_text(json.dumps({"holdings": holdings}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"source": "local portfolio file", "as_of": str(date.today()), "holdings": holdings, "saved": True}


def market_brief_response(event_limit: int):
    sources, errors = [], []
    indices, events = [], []
    try:
        frame = ak.stock_zh_index_spot_sina()
        wanted = {"sh000001", "sh000300", "sz399001", "sz399006"}
        subset = frame.loc[frame["代码"].astype(str).isin(wanted)]
        index_fields = [field for field in ["代码", "名称", "最新价", "涨跌额", "涨跌幅", "成交量", "成交额"] if field in subset]
        indices = [clean_record(item) for item in subset[index_fields].to_dict("records")]
        sources.append("AKShare stock_zh_index_spot_sina")
    except Exception as exc:
        errors.append(f"指数快照不可用：{exc}")
    try:
        frame = ak.stock_info_global_cls(symbol="全部")
        event_fields = [field for field in ["标题", "发布日期", "发布时间"] if field in frame]
        for item in frame.head(event_limit)[event_fields].to_dict("records"):
            events.append(clean_record(item))
        sources.append("AKShare stock_info_global_cls")
    except Exception as exc:
        errors.append(f"市场快讯不可用：{exc}")
    if not indices and not events:
        raise RuntimeError("；".join(errors) or "市场数据源未返回结果。")
    return {
        "source": "; ".join(sources), "as_of": str(date.today()), "indices": indices, "events": events,
        "unavailable": errors, "note": "快讯是待核验的事件线索，不等同于价格影响或投资结论。",
    }


def market_movers_response(limit: int):
    frame = ak.stock_zh_a_spot_tx()
    fields = [field for field in ["code", "name", "zxj", "zd", "zdf", "zdf_d5", "zdf_d20", "volume", "hsl", "pe_ttm", "zsz"] if field in frame]
    if "zdf" not in fields:
        raise ValueError("上游数据未提供涨跌幅字段。")
    frame = frame.copy()
    frame["zdf"] = pd.to_numeric(frame["zdf"], errors="coerce")
    frame = frame.dropna(subset=["zdf"])
    up = [clean_record(item) for item in frame.nlargest(limit, "zdf")[fields].to_dict("records")]
    down = [clean_record(item) for item in frame.nsmallest(limit, "zdf")[fields].to_dict("records")]
    return {
        "source": "AKShare stock_zh_a_spot_tx", "as_of": str(date.today()),
        "top_gainers": up, "top_losers": down,
        "note": "异动列表仅用于生成待研究候选池；涨跌幅和成交信息本身不构成推荐或预测。",
    }


def history(symbol: str, start: str = "", end: str = "", adjust: str = "qfq") -> pd.DataFrame:
    today = date.today()
    start_date = (pd.Timestamp(start).date() if start else today - timedelta(days=730)).strftime("%Y%m%d")
    end_date = (pd.Timestamp(end).date() if end else today).strftime("%Y%m%d")
    frame = ak.stock_zh_a_hist(
        symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust=adjust or "",
    )
    if frame.empty:
        raise ValueError("未返回历史行情；请确认代码、日期区间和数据源状态。")
    rename = {
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pct_change",
        "涨跌额": "change", "换手率": "turnover",
    }
    frame = frame.rename(columns=rename)
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ["open", "close", "high", "low", "volume", "amount", "amplitude", "pct_change", "change", "turnover"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("date").reset_index(drop=True)


def bars_response(symbol: str, start: str, end: str, adjust: str):
    frame = history(symbol, start, end, adjust)
    fields = [field for field in ["date", "open", "high", "low", "close", "volume", "amount", "turnover"] if field in frame]
    bars = []
    for row in frame[fields].to_dict("records"):
        row["date"] = iso_date(row["date"])
        bars.append(clean_record(row))
    return {
        "symbol": symbol, "source": "AKShare stock_zh_a_hist", "as_of": bars[-1]["date"],
        "adjust": adjust or "none", "bars": bars,
    }


def indicators_response(symbol: str, lookback: int, adjust: str):
    frame = history(symbol, "", "", adjust).tail(lookback).copy()
    close = frame["close"]
    frame["ma5"] = close.rolling(5).mean()
    frame["ma20"] = close.rolling(20).mean()
    frame["ma60"] = close.rolling(60).mean()
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = (-delta.clip(upper=0)).rolling(14).mean()
    frame["rsi14"] = 100 - 100 / (1 + gains / losses.replace(0, np.nan))
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    frame["macd"] = ema12 - ema26
    frame["macd_signal"] = frame["macd"].ewm(span=9, adjust=False).mean()
    frame["macd_histogram"] = frame["macd"] - frame["macd_signal"]
    returns = close.pct_change()
    drawdown = close / close.cummax() - 1
    last = frame.iloc[-1]
    latest = {key: to_number(last[key]) for key in ["close", "ma5", "ma20", "ma60", "rsi14", "macd", "macd_signal", "macd_histogram"]}
    return {
        "symbol": symbol, "source": "AKShare stock_zh_a_hist + local calculation", "as_of": iso_date(last["date"]),
        "adjust": adjust or "none", "lookback_trading_days": len(frame), "latest": latest,
        "annualized_volatility": to_number(returns.std() * math.sqrt(252)),
        "max_drawdown": to_number(drawdown.min()),
        "method_note": "RSI uses a 14-day simple rolling average; MACD uses EMA(12,26,9).",
    }


def spot_row(symbol: str) -> dict:
    frame = ak.stock_zh_a_spot_em()
    row = frame.loc[frame["代码"].astype(str).str.zfill(6) == symbol]
    if row.empty:
        raise ValueError("未找到该代码的行情快照。")
    return clean_record(row.iloc[0].to_dict())


def quote_response(symbol: str):
    row = spot_row(symbol)
    return {
        "symbol": symbol, "source": "AKShare stock_zh_a_spot_em", "as_of": str(row.get("更新时间") or date.today()),
        "note": "数据源快照，不是券商逐笔实时行情。", "quote": row,
    }


def valuation_response(symbol: str):
    row = spot_row(symbol)
    keys = ["代码", "名称", "最新价", "市盈率-动态", "市盈率-静态", "市净率", "总市值", "流通市值", "涨跌幅"]
    return {
        "symbol": symbol, "source": "AKShare stock_zh_a_spot_em", "as_of": str(row.get("更新时间") or date.today()),
        "valuation": {key: row.get(key) for key in keys if key in row},
        "note": "PE/PB口径由上游数据源决定；请在正式决策前复核口径和时点。",
    }


def fundamentals_response(symbol: str):
    frame = ak.stock_financial_analysis_indicator(symbol=symbol)
    if frame.empty:
        raise ValueError("数据源未返回财务分析指标。")
    frame = frame.head(8).copy()
    records = [clean_record(item) for item in frame.to_dict("records")]
    report_key = next((key for key in ["日期", "报告期", "报告日期"] if key in frame.columns), None)
    return {
        "symbol": symbol, "source": "AKShare stock_financial_analysis_indicator", "as_of": str(date.today()),
        "report_period_field": report_key, "records": records,
        "note": "字段和口径会随上游数据源变化；比较前确认报告期与合并/归母口径。",
    }


def announcements_response(symbol: str, start: str, end: str, limit: int):
    start_date = (pd.Timestamp(start).strftime("%Y%m%d") if start else (date.today() - timedelta(days=180)).strftime("%Y%m%d"))
    end_date = pd.Timestamp(end).strftime("%Y%m%d") if end else date.today().strftime("%Y%m%d")
    frame = ak.stock_zh_a_disclosure_report_cninfo(symbol=symbol, market="沪深京", start_date=start_date, end_date=end_date)
    if frame.empty:
        return {"symbol": symbol, "source": "CNINFO via AKShare", "as_of": str(date.today()), "announcements": []}
    records = [clean_record(item) for item in frame.head(limit).to_dict("records")]
    return {"symbol": symbol, "source": "CNINFO via AKShare", "as_of": str(date.today()), "announcements": records}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed, query = urlparse(self.path), parse_qs(urlparse(self.path).query)
        value = lambda key, default="": query.get(key, [default])[0]
        try:
            if parsed.path == "/health":
                return self.send_json(200, {"status": "ok", "bind": f"{HOST}:{PORT}", "mode": "research-only; local portfolio storage"})
            if parsed.path == "/v1/portfolio":
                return self.send_json(200, portfolio_response())
            if parsed.path == "/v1/market-brief":
                limit = min(max(int(value("event_limit", "20")), 1), 50)
                return self.send_json(200, market_brief_response(limit))
            if parsed.path == "/v1/market-movers":
                limit = min(max(int(value("limit", "20")), 1), 50)
                return self.send_json(200, market_movers_response(limit))
            symbol = value("symbol")
            if not symbol.isdigit() or len(symbol) != 6:
                raise ValueError("symbol 必须是6位A股代码。")
            if parsed.path == "/v1/quote": payload = quote_response(symbol)
            elif parsed.path == "/v1/ohlcv": payload = bars_response(symbol, value("start"), value("end"), value("adjust", "qfq"))
            elif parsed.path == "/v1/indicators": payload = indicators_response(symbol, int(value("lookback", "260")), value("adjust", "qfq"))
            elif parsed.path == "/v1/fundamentals": payload = fundamentals_response(symbol)
            elif parsed.path == "/v1/announcements": payload = announcements_response(symbol, value("start"), value("end"), min(max(int(value("limit", "20")), 1), 100))
            elif parsed.path == "/v1/valuation": payload = valuation_response(symbol)
            else: return self.send_json(404, {"error": "Unknown endpoint"})
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(502, {"error": f"数据查询失败：{exc}", "hint": "检查代码、网络、AKShare版本和数据源状态；不要用模型补写缺失数据。"})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path != "/v1/portfolio":
                return self.send_json(404, {"error": "Unknown endpoint"})
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 100_000:
                raise ValueError("请求体大小无效。")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            self.send_json(200, save_portfolio(payload))
        except Exception as exc:
            self.send_json(400, {"error": f"保存持仓失败：{exc}"})


if __name__ == "__main__":
    print(f"Serving on http://{HOST}:{PORT} (read-only, Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
