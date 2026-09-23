#!/usr/bin/env python
"""Small, auditable A-share intraday factor research and ranking pipeline."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "factor-lab"
BARS = STORE / "bars"
EVALUATION = STORE / "evaluation.json"
MODEL = STORE / "model.json"
LATEST = STORE / "latest-ranking.json"
CHINA = timezone(timedelta(hours=8))
SLOTS = ("09:35", "10:30", "11:30", "13:30", "14:00", "14:30", "14:50")
FACTOR_NAMES = {
    "open_momentum": "开盘以来动量",
    "gap": "开盘缺口",
    "range_position": "日内区间位置",
    "range_width": "日内振幅",
    "vwap_gap": "相对日内均价",
}
MIN_STOCKS = 12
MIN_DAYS = 12
UNIVERSE_SIZE = 40
EVALUATION_VERSION = 2
EM_MINUTE_UNAVAILABLE = threading.Event()
SINA_MINUTE_UNAVAILABLE = threading.Event()
SINA_REQUEST_GATE = threading.Lock()


def now_cn() -> datetime:
    return datetime.now(CHINA)


def log(message: str):
    print(message, flush=True)


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def valid_number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def spot_frame() -> pd.DataFrame:
    import akshare as ak

    try:
        frame = ak.stock_zh_a_spot_em()
        source = "东方财富 stock_zh_a_spot_em"
    except Exception as primary_error:
        log(f"  东方财富 A 股快照连接失败：{primary_error}")
        log("  正在尝试新浪 A 股快照备用源（首次获取可能较慢）...")
        try:
            frame = ak.stock_zh_a_spot()
            source = "新浪 stock_zh_a_spot"
            frame = frame.copy()
            frame["代码"] = frame["代码"].astype(str).str.extract(r"(\d{6})$")[0]
        except Exception as fallback_error:
            raise RuntimeError(f"A股快照的东方财富与新浪数据源均不可用。主源：{primary_error}；备用源：{fallback_error}") from fallback_error
    needed = {"代码", "名称", "最新价", "今开", "昨收", "最高", "最低", "成交额", "成交量"}
    missing = needed - set(frame.columns)
    if missing:
        raise RuntimeError("A股行情字段缺失：" + "、".join(sorted(missing)))
    result = frame.copy()
    result.attrs["source"] = source
    log(f"  A 股快照来源：{source}，共 {len(result)} 条")
    return result


def choose_universe(frame: pd.DataFrame) -> list[dict]:
    rows = frame.copy()
    rows["成交额"] = pd.to_numeric(rows["成交额"], errors="coerce")
    rows = rows[rows["代码"].astype(str).str.fullmatch(r"[036]\d{5}")]
    rows = rows[~rows["名称"].astype(str).str.upper().str.contains("ST|退")]
    if (rows["成交额"] > 0).sum() >= MIN_STOCKS:
        rows = rows[rows["成交额"] > 0].nlargest(UNIVERSE_SIZE, "成交额")
    elif "总市值" in rows.columns:
        rows["总市值"] = pd.to_numeric(rows["总市值"], errors="coerce")
        rows = rows[rows["总市值"] > 0].nlargest(UNIVERSE_SIZE, "总市值")
    else:
        return []
    return [{"symbol": str(row["代码"]), "name": str(row["名称"])} for _, row in rows.iterrows()]


def normalized_bars(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {"time": ("时间", "日期", "datetime", "day", "time"), "open": ("开盘", "open"), "high": ("最高", "high"),
               "low": ("最低", "low"), "close": ("收盘", "close"), "volume": ("成交量", "volume"),
               "amount": ("成交额", "amount")}
    columns = {key: next((item for item in names if item in frame.columns), None) for key, names in aliases.items()}
    if any(value is None for value in columns.values()):
        raise RuntimeError("5分钟历史行情缺少时间或 OHLCV 字段。")
    result = pd.DataFrame({key: frame[value] for key, value in columns.items()})
    result["time"] = pd.to_datetime(result["time"], errors="coerce")
    for key in ("open", "high", "low", "close", "volume", "amount"):
        result[key] = pd.to_numeric(result[key], errors="coerce")
    return result.dropna().sort_values("time").drop_duplicates("time")


def sina_minute_frame(symbol: str) -> pd.DataFrame:
    """Read Sina's public 5-minute JSONP with an explicit network timeout."""
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
    params = {"symbol": symbol, "scale": "5", "ma": "no", "datalen": "1970"}
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    body = response.text
    opening, closing = body.find("=("), body.rfind(");")
    if opening < 0 or closing <= opening:
        raise ValueError("新浪分钟线返回格式无法解析")
    payload = json.loads(body[opening + 2:closing])
    if not isinstance(payload, list):
        raise ValueError("新浪分钟线未返回K线数组")
    return pd.DataFrame(payload)


def fetch_bars(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    import akshare as ak

    primary_error = None
    if not EM_MINUTE_UNAVAILABLE.is_set():
        try:
            raw = ak.stock_zh_a_hist_min_em(symbol=symbol, period="5", start_date=start.strftime("%Y-%m-%d 09:30:00"),
                                            end_date=end.strftime("%Y-%m-%d 15:00:00"), adjust="")
            result = normalized_bars(raw)
            if not result.empty:
                return result
            primary_error = ValueError("东方财富未返回分钟线")
        except Exception as exc:
            primary_error = exc
            if isinstance(exc, (OSError, ConnectionError, TimeoutError)) or "HTTPSConnectionPool" in str(exc):
                if not EM_MINUTE_UNAVAILABLE.is_set():
                    log(f"  东方财富分钟线连接失败，后续股票改用新浪备用源：{exc}")
                EM_MINUTE_UNAVAILABLE.set()
    exchange = "sh" if symbol.startswith("6") else "sz"
    if SINA_MINUTE_UNAVAILABLE.is_set():
        raise RuntimeError(f"{symbol} 的东方财富与新浪分钟线连接均不可用；已停止重复请求备用源。")
    try:
        # Sina supplies at most a recent rolling window; clip to our requested period.
        with SINA_REQUEST_GATE:
            if SINA_MINUTE_UNAVAILABLE.is_set():
                raise RuntimeError("新浪备用源已不可用")
            raw = sina_minute_frame(f"{exchange}{symbol}")
            time.sleep(0.2)
        result = normalized_bars(raw)
        result = result[(result["time"] >= pd.Timestamp(start.date())) &
                        (result["time"] <= pd.Timestamp(end.date()) + pd.Timedelta(days=1))]
        if result.empty:
            raise ValueError("新浪未返回请求时段内的5分钟线")
        return result
    except Exception as fallback_error:
        if isinstance(fallback_error, (requests.RequestException, OSError, ConnectionError, TimeoutError)):
            if not SINA_MINUTE_UNAVAILABLE.is_set():
                log(f"  新浪分钟线也无法连接，停止其余备用请求：{fallback_error}")
            SINA_MINUTE_UNAVAILABLE.set()
        raise RuntimeError(f"{symbol} 的东方财富与新浪5分钟线均不可用。主源：{primary_error or '已切换备用源'}；备用源：{fallback_error}") from fallback_error


def refresh_bars(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    cache_path = BARS / f"{symbol}.csv"
    previous = pd.DataFrame()
    if cache_path.exists():
        try:
            previous = normalized_bars(pd.read_csv(cache_path))
        except (OSError, ValueError, RuntimeError):
            previous = pd.DataFrame()
    fetch_start = start
    if not previous.empty:
        last = previous["time"].max().to_pydatetime().replace(tzinfo=CHINA) - timedelta(days=1)
        fetch_start = max(start, last)
    fresh = fetch_bars(symbol, fetch_start, end)
    if fresh.empty and previous.empty:
        return fresh
    combined = fresh if previous.empty else normalized_bars(pd.concat((previous, fresh), ignore_index=True))
    combined = combined[combined["time"] >= pd.Timestamp(start.date())]
    combined.to_csv(cache_path, index=False)
    return combined


def history_samples(symbol: str, bars: pd.DataFrame) -> list[dict]:
    if bars.empty:
        return []
    frame = bars.copy()
    frame["day"] = frame["time"].dt.strftime("%Y-%m-%d")
    complete_days = [(day, group.reset_index(drop=True)) for day, group in frame.groupby("day", sort=True)
                     if len(group) >= 35 and group.iloc[-1]["time"].strftime("%H:%M") >= "14:55"]
    samples = []
    for index, (day, group) in enumerate(complete_days):
        if index == 0:
            continue
        prev_close = valid_number(complete_days[index - 1][1].iloc[-1]["close"])
        close = valid_number(group.iloc[-1]["close"])
        opening = valid_number(group.iloc[0]["open"])
        if not prev_close or not close or not opening:
            continue
        for slot in SLOTS:
            earlier = group[group["time"].dt.strftime("%H:%M") <= slot]
            if earlier.empty or earlier.iloc[-1]["time"].strftime("%H:%M") < slot:
                continue
            price = valid_number(earlier.iloc[-1]["close"])
            high = valid_number(earlier["high"].max())
            low = valid_number(earlier["low"].min())
            volume = earlier["volume"].clip(lower=0)
            vwap = inferred_vwap(earlier["amount"].clip(lower=0).sum(), volume.sum(), price)
            factors = factor_values(price, opening, prev_close, high, low, vwap)
            if factors is None:
                continue
            samples.append({"symbol": symbol, "day": day, "slot": slot,
                            "target": close / price - 1, **factors})
    return samples


def factor_values(price, opening, prev_close, high, low, vwap) -> dict | None:
    values = [valid_number(value) for value in (price, opening, prev_close, high, low, vwap)]
    if any(value is None or value <= 0 for value in values):
        return None
    price, opening, prev_close, high, low, vwap = values
    if high < low or not low <= price <= high * 1.001:
        return None
    return {
        "open_momentum": price / opening - 1,
        "gap": opening / prev_close - 1,
        "range_position": (price - low) / max(high - low, 0.001) - 0.5,
        "range_width": (high - low) / opening,
        "vwap_gap": price / vwap - 1,
    }


def inferred_vwap(amount, volume, reference) -> float | None:
    amount, volume, reference = (valid_number(value) for value in (amount, volume, reference))
    if not amount or not volume or not reference or min(amount, volume, reference) <= 0:
        return None
    choices = (amount / volume, amount / (volume * 100))
    value = min(choices, key=lambda item: abs(item / reference - 1))
    return value if 0.8 <= value / reference <= 1.2 else None


def slot_ic(frame: pd.DataFrame, factor: str) -> list[dict]:
    results = []
    for (day, slot), group in frame.groupby(["day", "slot"]):
        usable = group[[factor, "target"]].dropna()
        if len(usable) < MIN_STOCKS or usable[factor].nunique() < 5 or usable["target"].nunique() < 5:
            continue
        # Spearman is Pearson correlation of average ranks; no optional scipy.
        ic = valid_number(usable[factor].rank().corr(usable["target"].rank()))
        if ic is not None:
            results.append({"day": day, "slot": slot, "ic": ic, "count": len(usable)})
    return results


def evaluate(samples: list[dict], universe: list[dict], failed: list[str]) -> dict:
    frame = pd.DataFrame(samples)
    if frame.empty:
        raise RuntimeError("没有可验证的完整盘中样本；请检查5分钟行情数据。")
    days = sorted(frame["day"].unique())
    if len(days) < MIN_DAYS:
        raise RuntimeError(f"只有 {len(days)} 个完整历史交易日，至少需要 {MIN_DAYS} 日。")
    train_end = max(1, int(len(days) * 0.6))
    validation_end = max(train_end + 2, int(len(days) * 0.8))
    train_days = set(days[:train_end])
    validation_days = set(days[train_end:validation_end])
    holdout_days = set(days[validation_end:])
    factors = []
    for key, name in FACTOR_NAMES.items():
        train = [row for row in slot_ic(frame[frame["day"].isin(train_days)], key)]
        validation = [row for row in slot_ic(frame[frame["day"].isin(validation_days)], key)]
        holdout = [row for row in slot_ic(frame[frame["day"].isin(holdout_days)], key)]
        if len(train) < 4 or len(validation) < 2 or len(holdout) < 2:
            factor = {"key": key, "name": name, "eligible": False, "reason": "可计算的日期/时段样本不足"}
        else:
            train_ic = float(np.mean([item["ic"] for item in train]))
            validation_ic = float(np.mean([item["ic"] for item in validation]))
            holdout_ic = float(np.mean([item["ic"] for item in holdout]))
            direction = 1 if train_ic >= 0 else -1
            validation_signed = validation_ic * direction
            positive_share = float(np.mean([item["ic"] * direction > 0 for item in validation]))
            validated = abs(train_ic) >= 0.02 and validation_signed >= 0.02 and positive_share >= 0.5
            exploratory = abs(train_ic) >= 0.01 and validation_signed >= 0.04 and positive_share >= 0.6
            factor = {"key": key, "name": name, "train_ic": round(train_ic, 4),
                      "validation_ic": round(validation_ic, 4),
                      "holdout_ic": round(holdout_ic, 4), "direction": direction,
                      "validation_positive_share": round(positive_share, 3),
                      "holdout_positive_share": round(float(np.mean([item["ic"] * direction > 0 for item in holdout])), 3),
                      "train_groups": len(train), "validation_groups": len(validation), "holdout_groups": len(holdout),
                      "eligible": validated, "exploratory": exploratory,
                      "tier": "validated" if validated else ("exploratory" if exploratory else "rejected")}
            if not factor["eligible"]:
                factor["reason"] = "未达正式门槛，仅供模拟研究" if exploratory else "训练或验证样本的方向/稳定性未达门槛"
        factors.append(factor)
    factors.sort(key=lambda item: item.get("validation_ic", 0) * item.get("direction", 1), reverse=True)
    correlations = frame[frame["day"].isin(train_days)][list(FACTOR_NAMES)].rank().corr().fillna(0)
    return {"trained_at": now_cn().isoformat(timespec="seconds"), "market": "a",
            "eligibility_version": EVALUATION_VERSION,
            "target": "历史5分钟时点至同日收盘的价格变化", "source": "AKShare stock_zh_a_hist_min_em",
            "universe_method": "训练启动时成交额前40名；这是受限候选池，不代表全A股，也有存续偏差",
            "universe": universe, "days": len(days), "first_day": days[0], "last_day": days[-1],
            "train_days": len(train_days), "validation_days": len(validation_days),
            "holdout_days": len(holdout_days), "samples": len(samples),
            "failed_symbols": failed, "factors": factors,
            "correlations": {first: {second: round(float(correlations.loc[first, second]), 3)
                                     for second in FACTOR_NAMES if second != first} for first in FACTOR_NAMES}}


def train() -> dict:
    log("[1/4] 获取 A 股行情并选择首版40只候选股票...")
    EM_MINUTE_UNAVAILABLE.clear()
    SINA_MINUTE_UNAVAILABLE.clear()
    snapshot = spot_frame()
    universe = choose_universe(snapshot)
    method = ("训练启动时成交额前40名" if (pd.to_numeric(snapshot["成交额"], errors="coerce") > 0).sum() >= MIN_STOCKS
              else "盘前成交额不可用时按总市值前40名")
    log("  候选池口径：" + method)
    if len(universe) < MIN_STOCKS:
        previous = read_json(MODEL) or read_json(EVALUATION)
        cached = previous.get("universe", [])
        previous_time = previous.get("selected_at") or previous.get("trained_at")
        try:
            recent_cache = previous_time and (now_cn() - datetime.fromisoformat(previous_time)).days <= 30
        except (TypeError, ValueError):
            recent_cache = False
        if len(cached) >= MIN_STOCKS and recent_cache:
            universe = cached[:UNIVERSE_SIZE]
            method = "盘前成交额不可用，沿用上次训练的候选池"
            log("  " + method + f"（{len(universe)}只；可能存在候选池时效偏差）")
        else:
            raise RuntimeError("当前快照没有足够的成交额/市值数据，且没有历史候选池；请在交易时段或收盘后重试训练。")
    STORE.mkdir(parents=True, exist_ok=True)
    BARS.mkdir(parents=True, exist_ok=True)
    start, end = now_cn() - timedelta(days=65), now_cn()
    log(f"[2/4] 下载近65日5分钟行情：共 {len(universe)} 只；数据源可能只返回近期记录。")
    samples, failed = [], []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(refresh_bars, item["symbol"], start, end): item for item in universe}
        for index, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                bars = future.result()
                stock_samples = history_samples(item["symbol"], bars[bars["time"].dt.date < now_cn().date()])
                samples.extend(stock_samples)
                log(f"  {index}/{len(universe)} {item['symbol']}：{len(bars)} 根历史K线，{len(stock_samples)} 条有效时点样本")
            except Exception as exc:
                failed.append(f"{item['symbol']}: {exc}")
                log(f"  {index}/{len(universe)} {item['symbol']}：获取失败：{exc}")
    log(f"[3/4] 验证 {len(samples)} 条样本，按交易日拆分训练、验证与留出窗口...")
    if len(failed) >= len(universe) - MIN_STOCKS + 1:
        log(f"  {len(failed)}/{len(universe)} 只股票取数失败；首条失败：{failed[0]}")
    try:
        result = evaluate(samples, universe, failed)
    except RuntimeError as exc:
        detail = "；".join(failed[:2])
        suffix = f"；{len(failed)}/{len(universe)}只取数失败；示例：{detail}" if failed else ""
        raise RuntimeError(f"{exc}{suffix}") from exc
    result["spot_source"] = snapshot.attrs.get("source", "未知")
    result["minute_source"] = "新浪备用源（东方财富连接失败）" if EM_MINUTE_UNAVAILABLE.is_set() else "东方财富主源，个别股票可能使用新浪备用源"
    result["universe_method"] = method + "；这是受限候选池，不代表全A股，也有存续偏差"
    write_json(EVALUATION, result)
    log("[4/4] 训练完成；点击“获得因子”查看正式或探索性评估结果。")
    return result


def refresh_evaluation_from_cache(previous: dict) -> dict:
    """Re-score a legacy evaluation without making 40 network requests."""
    universe = previous.get("universe", [])
    if len(universe) < MIN_STOCKS:
        raise RuntimeError("旧训练结果缺少候选池；请重新点击“开启训练”。")
    samples, failed = [], []
    cutoff = now_cn().date()
    for item in universe:
        symbol = item.get("symbol")
        try:
            bars = normalized_bars(pd.read_csv(BARS / f"{symbol}.csv"))
            samples.extend(history_samples(symbol, bars[bars["time"].dt.date < cutoff]))
        except (OSError, ValueError, RuntimeError) as exc:
            failed.append(f"{symbol}: {exc}")
    try:
        updated = evaluate(samples, universe, failed)
    except RuntimeError as exc:
        raise RuntimeError(f"本地缓存不足以重新评估因子：{exc}；请重新点击“开启训练”。") from exc
    updated["trained_at"] = previous.get("trained_at", updated["trained_at"])
    updated["re_evaluated_at"] = now_cn().isoformat(timespec="seconds")
    for field in ("universe_method", "spot_source", "minute_source"):
        if field in previous:
            updated[field] = previous[field]
    write_json(EVALUATION, updated)
    log(f"已用本地缓存重新评估 {updated['samples']} 条样本；未重新下载行情。")
    return updated


def select_factors() -> dict:
    result = read_json(EVALUATION)
    if not result:
        raise RuntimeError("尚无训练结果，请先点击“开启训练”。")
    if result.get("eligibility_version") != EVALUATION_VERSION:
        log("检测到旧版评估结果；正用本地 K 线缓存重新计算筛选层级...")
        result = refresh_evaluation_from_cache(result)
    try:
        days_since_data = (now_cn().date() - pd.Timestamp(result["last_day"]).date()).days
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("训练结果缺少有效数据日期；请重新点击“开启训练”。")
    if days_since_data > 7:
        raise RuntimeError(f"训练数据已滞后 {days_since_data} 天；请重新点击“开启训练”。")
    eligible = [item for item in result["factors"] if item.get("eligible")]
    quality = "validated"
    if not eligible:
        eligible = [item for item in result["factors"] if item.get("exploratory")]
        quality = "exploratory"
    if not eligible:
        details = "；".join(f"{item['name']} 训练IC={item.get('train_ic', '不足')} 验证IC={item.get('validation_ic', '不足')}" for item in result["factors"])
        raise RuntimeError("无正式或探索性因子满足训练/验证门槛；" + details)
    chosen = []
    for item in eligible:
        if all(abs(result["correlations"][item["key"]][picked["key"]]) < 0.85 for picked in chosen):
            chosen.append(item)
        if len(chosen) == 3:
            break
    if not chosen:
        raise RuntimeError("有效因子高度相关，无法组成模型。")
    weights = [abs(item["validation_ic"]) for item in chosen]
    total = sum(weights)
    model = {"model_id": now_cn().strftime("%Y%m%d-%H%M%S"), "selected_at": now_cn().isoformat(timespec="seconds"),
             "market": "a", "target": result["target"], "universe_method": result["universe_method"],
             "quality": quality, "selection_warning": ("仅供模拟研究；本次较弱门槛是在查看历史结果后引入，留出集不再算独立验证。" if quality == "exploratory" else ""),
             "universe": result["universe"], "training": {"days": result["days"], "last_day": result["last_day"],
             "samples": result["samples"], "holdout_days": result["holdout_days"]},
             "factors": [{**item, "weight": round(weight / total, 5)} for item, weight in zip(chosen, weights)]}
    write_json(MODEL, model)
    label = "正式通过" if quality == "validated" else "探索性，非正式验证"
    log("已发布" + label + "模型 " + model["model_id"] + "：" + "、".join(item["name"] for item in chosen))
    if quality == "exploratory":
        log("警告：" + model["selection_warning"])
    return model


def live_factors(row: pd.Series) -> dict | None:
    price, opening, prev_close, high, low = (valid_number(row.get(key)) for key in
                                             ("最新价", "今开", "昨收", "最高", "最低"))
    amount, volume = valid_number(row.get("成交额")), valid_number(row.get("成交量"))
    vwap = inferred_vwap(amount, volume, price)
    return factor_values(price, opening, prev_close, high, low, vwap)


def is_trading_time(moment: datetime) -> bool:
    clock = moment.strftime("%H:%M")
    return moment.weekday() < 5 and ("09:35" <= clock <= "11:30" or "13:00" <= clock < "15:00")


def rank() -> dict:
    model = read_json(MODEL)
    if not model:
        raise RuntimeError("尚无已发布因子，请先训练并点击“获得因子”。")
    started = now_cn()
    if not is_trading_time(started):
        raise RuntimeError("仅在 A 股交易时段 09:35–11:30、13:00–15:00 生成点击至收盘排名。")
    import akshare as ak

    calendar = ak.tool_trade_date_hist_sina()
    calendar_key = next((key for key in ("trade_date", "日期") if key in calendar.columns), None)
    if calendar_key is None or started.date() not in set(pd.to_datetime(calendar[calendar_key], errors="coerce").dt.date.dropna()):
        raise RuntimeError("交易日历未确认今天为 A 股交易日；本次不发布排名。")
    if (started - datetime.fromisoformat(model["selected_at"])).days > 30:
        raise RuntimeError("因子模型已超过30天，请重新训练并获得因子。")
    if model.get("quality") == "exploratory" and (started.date() - pd.Timestamp(model["training"]["last_day"]).date()).days > 7:
        raise RuntimeError("探索性模型的训练数据已超过7天；请重新训练并获得因子。")
    log("[1/3] 获取点击时刻的 A 股行情快照...")
    frame = spot_frame()
    fetched = now_cn()
    if fetched.date() != started.date() or not is_trading_time(fetched):
        raise RuntimeError("行情获取跨过交易时段，请重新点击推荐。")
    allowed = {item["symbol"] for item in model["universe"]}
    frame = frame[frame["代码"].astype(str).isin(allowed)].copy()
    records = []
    for _, row in frame.iterrows():
        factors = live_factors(row)
        if factors:
            records.append({"symbol": str(row["代码"]), "name": str(row["名称"]),
                            "price": valid_number(row["最新价"]), **factors})
    if len(records) < MIN_STOCKS:
        raise RuntimeError(f"当前仅 {len(records)} 只候选有完整有效行情，至少需要 {MIN_STOCKS} 只。")
    data = pd.DataFrame(records)
    log(f"[2/3] 在 {len(data)} 只有效候选中用模型 {model['model_id']} 排序...")
    data["score"] = 0.0
    for factor in model["factors"]:
        key = factor["key"]
        data["score"] += (data[key].rank(pct=True) - 0.5) * factor["direction"] * factor["weight"]
    data = data.sort_values("score", ascending=False)
    ranking = []
    for position, (_, row) in enumerate(data.head(20).iterrows(), 1):
        ranking.append({"rank": position, "symbol": row["symbol"], "name": row["name"],
                        "price": round(float(row["price"]), 3), "score": round(float(row["score"]), 4),
                        "factors": {item["key"]: round(float(row[item["key"]]), 5) for item in model["factors"]}})
    payload = {"id": fetched.strftime("%Y%m%d-%H%M%S-%f"), "market": "a", "generated_at": fetched.isoformat(timespec="seconds"),
               "model_id": model["model_id"], "model_quality": model.get("quality", "validated"),
               "candidate_count": len(data), "ranking": ranking,
               "factors": [{"key": item["key"], "name": item["name"], "weight": item["weight"],
                            "direction": item["direction"], "holdout_ic": item["holdout_ic"]} for item in model["factors"]],
               "universe_method": model["universe_method"],
               "note": ("预测目标是快照时刻至当日收盘；AKShare全市场快照未提供逐股行情时间戳，无法验证逐股延迟。排名是研究结果，不是收益概率或买入指令。"
                        + model.get("selection_warning", ""))}
    write_json(STORE / "rankings" / f"{payload['id']}.json", payload)
    write_json(LATEST, payload)
    log("[3/3] 排名已归档并写入日报页面数据。")
    try:
        request = Request("http://127.0.0.1:8766/api/publish-ranking", data=b"{}", method="POST",
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=4):
            log("日报页面已收到最新排名通知。")
    except OSError as exc:
        log(f"日报页面通知暂不可用；打开或刷新页面仍可读取排名：{exc}")
    return payload


def status() -> dict:
    evaluation, model, latest = read_json(EVALUATION), read_json(MODEL), read_json(LATEST)
    return {"trained_at": evaluation.get("trained_at"), "model_id": model.get("model_id"),
            "selected_at": model.get("selected_at"), "quality": model.get("quality"),
            "factors": [item["name"] for item in model.get("factors", [])],
            "latest_ranking_at": latest.get("generated_at"), "latest_count": latest.get("candidate_count")}


def main() -> int:
    parser = argparse.ArgumentParser(description="A股盘中因子训练、选择和即时排名")
    parser.add_argument("action", choices=("train", "select", "rank", "status"))
    args = parser.parse_args()
    try:
        result = {"train": train, "select": select_factors, "rank": rank, "status": status}[args.action]()
        if args.action == "status":
            log(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        log("错误：" + str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
