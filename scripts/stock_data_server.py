#!/usr/bin/env python
"""Read-only local A-share data adapter for dsh-finance-agent.

Run on Windows: .\\.venv\\Scripts\\python.exe .\\scripts\\stock_data_server.py
The server intentionally binds only to 127.0.0.1 and contains no broker API.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
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
EVENT_ARCHIVE_FILE = PROJECT_ROOT / "data" / "market-events.json"


def safe_float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def first_value(record: dict, *candidates):
    """Return a value by exact key first, then by a case-insensitive substring."""
    for key in candidates:
        if key in record and record[key] not in (None, ""):
            return record[key]
    lowered = [(str(key).lower(), value) for key, value in record.items()]
    for candidate in candidates:
        needle = candidate.lower()
        for key, value in lowered:
            if needle in key and value not in (None, ""):
                return value
    return None


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


def stock_search_response(query: str, limit: int):
    query = str(query or "").strip()
    if not query:
        raise ValueError("query 不能为空。")
    frame = ak.stock_info_a_code_name().copy()
    code_key = next((key for key in ["code", "代码", "证券代码"] if key in frame.columns), None)
    name_key = next((key for key in ["name", "名称", "证券简称"] if key in frame.columns), None)
    if not code_key or not name_key:
        raise ValueError("上游股票列表字段发生变化。")
    frame[code_key] = frame[code_key].astype(str).str.zfill(6)
    exact = frame[code_key].eq(query) | frame[name_key].astype(str).eq(query)
    partial = frame[code_key].str.contains(query, case=False, na=False, regex=False) | frame[name_key].astype(str).str.contains(query, case=False, na=False, regex=False)
    result = pd.concat([frame.loc[exact], frame.loc[partial & ~exact]]).head(limit)
    matches = [{"symbol": str(item[code_key]), "name": str(item[name_key])} for item in result.to_dict("records")]
    return {"query": query, "source": "AKShare stock_info_a_code_name", "as_of": str(date.today()), "matches": matches}


def trading_day_response(day_text: str = ""):
    target = pd.Timestamp(day_text).date() if day_text else date.today()
    frame = ak.tool_trade_date_hist_sina()
    key = next((key for key in ["trade_date", "日期"] if key in frame.columns), None)
    if not key:
        raise ValueError("上游交易日历字段发生变化。")
    dates = {pd.Timestamp(value).date() for value in frame[key].dropna()}
    return {
        "date": str(target), "is_trading_day": target in dates,
        "source": "AKShare tool_trade_date_hist_sina", "as_of": str(max(dates)) if dates else None,
    }


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


def market_events_response(days: int, limit: int):
    frame = ak.stock_info_global_cls(symbol="全部").copy()
    title_key = next((key for key in ["标题", "title", "内容"] if key in frame.columns), None)
    date_key = next((key for key in ["发布日期", "日期", "date"] if key in frame.columns), None)
    time_key = next((key for key in ["发布时间", "时间", "time"] if key in frame.columns), None)
    if not title_key:
        raise ValueError("上游快讯未提供标题字段。")
    cutoff = date.today() - timedelta(days=days - 1)
    fresh_events = []
    for item in frame.to_dict("records"):
        event_date = None
        if date_key and item.get(date_key) not in (None, ""):
            try:
                event_date = pd.Timestamp(item[date_key]).date()
            except (TypeError, ValueError):
                pass
        theme, path = classify_market_event(str(item.get(title_key, "")))
        fresh_events.append({
            "title": str(item.get(title_key, "")),
            "date": str(event_date) if event_date else None,
            "time": str(item.get(time_key, "")) if time_key else None,
            "theme": theme, "possible_impact_path": path,
        })
    archived_events = []
    if EVENT_ARCHIVE_FILE.exists():
        try:
            archived_events = json.loads(EVENT_ARCHIVE_FILE.read_text(encoding="utf-8")).get("events", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            archived_events = []
    combined = {}
    for event in archived_events + fresh_events:
        if not isinstance(event, dict) or not event.get("title"):
            continue
        combined[(event.get("date"), event.get("time"), event["title"])] = event
    retained = []
    archive_cutoff = date.today() - timedelta(days=89)
    for event in combined.values():
        try:
            event_date = pd.Timestamp(event.get("date")).date() if event.get("date") else None
        except (TypeError, ValueError):
            event_date = None
        if event_date is None or event_date >= archive_cutoff:
            retained.append(event)
    retained.sort(key=lambda event: (event.get("date") or "", event.get("time") or ""), reverse=True)
    EVENT_ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVENT_ARCHIVE_FILE.write_text(json.dumps({"events": retained}, ensure_ascii=False, indent=2), encoding="utf-8")
    events = []
    for event in retained:
        try:
            event_date = pd.Timestamp(event.get("date")).date() if event.get("date") else None
        except (TypeError, ValueError):
            event_date = None
        if event_date and event_date < cutoff:
            continue
        events.append(event)
        if len(events) >= limit:
            break
    return {
        "window_days": days, "window_start": str(cutoff), "window_end": str(date.today()),
        "source": "AKShare stock_info_global_cls", "as_of": str(date.today()), "events": events,
        "note": "快讯为事件线索；本地滚动归档90日，涉及投资判断前应核验公告或权威原文。",
    }


def classify_market_event(title: str):
    groups = [
        (("央行", "利率", "降准", "流动性", "社融", "人民币"), "宏观与流动性", "可能经利率、汇率和风险偏好影响大盘估值；方向需结合正式政策与市场预期差核验。"),
        (("芯片", "半导体", "人工智能", "AI", "算力", "机器人"), "科技", "可能影响科技产业链订单与估值，但主题热度不能替代业绩兑现。"),
        (("地产", "房地产", "住房", "房贷"), "房地产", "可能经销售、信用和地方财政影响地产链及金融板块，需关注政策落地与基本面数据。"),
        (("消费", "旅游", "零售", "白酒", "汽车"), "消费", "可能影响消费需求预期，需用销量、价格和企业财报验证持续性。"),
        (("原油", "煤炭", "电力", "新能源", "光伏", "锂"), "能源", "可能经商品价格、成本和产能影响上下游利润，需同时评估反向价格风险。"),
        (("医药", "医疗", "药品", "创新药"), "医药", "可能影响研发、准入与需求预期，需核验监管原文、临床证据和商业化进展。"),
        (("关税", "制裁", "战争", "冲突", "出口", "贸易"), "外部风险", "可能经出口需求、供应链与风险偏好传导，事件演变和政策回应存在较大不确定性。"),
    ]
    lowered = title.lower()
    for keywords, theme, path in groups:
        if any(keyword.lower() in lowered for keyword in keywords):
            return theme, path
    return "综合事件", "与A股的直接传导关系尚待核验；先确认权威原文、涉及行业和可量化影响。"


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


def candidate_ranking_response(limit: int):
    frame = ak.stock_zh_a_spot_tx().copy()
    aliases = {
        "symbol": ["code", "代码"], "name": ["name", "名称"], "price": ["zxj", "最新价"],
        "day": ["zdf", "涨跌幅"], "day5": ["zdf_d5", "5日涨跌幅"],
        "day20": ["zdf_d20", "20日涨跌幅"], "turnover": ["hsl", "换手率"],
        "pe": ["pe_ttm", "市盈率-动态"], "market_cap": ["zsz", "总市值"],
    }
    columns = {name: next((key for key in keys if key in frame.columns), None) for name, keys in aliases.items()}
    if not columns["symbol"] or not columns["name"] or not columns["day"]:
        raise ValueError("上游行情缺少代码、名称或涨跌幅字段。")
    rows = []
    for item in frame.to_dict("records"):
        raw_symbol = str(item.get(columns["symbol"], ""))
        digits = "".join(character for character in raw_symbol if character.isdigit())
        symbol = digits[-6:] if len(digits) >= 6 else digits.zfill(6)
        name = str(item.get(columns["name"], ""))
        day = safe_float(item.get(columns["day"])) if columns["day"] else None
        day5 = safe_float(item.get(columns["day5"])) if columns["day5"] else None
        day20 = safe_float(item.get(columns["day20"])) if columns["day20"] else None
        turnover = safe_float(item.get(columns["turnover"])) if columns["turnover"] else None
        pe = safe_float(item.get(columns["pe"])) if columns["pe"] else None
        if len(symbol) != 6 or day is None or "ST" in name.upper() or day >= 9.8:
            continue
        # Transparent research score: balanced momentum, tradability and non-extreme valuation.
        momentum = max(-10, min(10, day)) * 1.2
        momentum += max(-20, min(20, day5 or 0)) * 0.8
        momentum += max(-40, min(40, day20 or 0)) * 0.35
        liquidity = 10 if turnover is not None and 1 <= turnover <= 12 else 3
        valuation = 10 if pe is not None and 0 < pe <= 40 else (5 if pe is not None and 0 < pe <= 80 else 0)
        score = round(max(0, min(100, 50 + momentum + liquidity + valuation)), 2)
        rows.append({
            "symbol": symbol, "name": name,
            "price": to_number(item.get(columns["price"])) if columns["price"] else None,
            "pct_change_1d": day, "pct_change_5d": day5, "pct_change_20d": day20,
            "turnover": turnover, "pe_ttm": pe,
            "market_cap": to_number(item.get(columns["market_cap"])) if columns["market_cap"] else None,
            "score": score,
            "reason": f"1/5/20日表现为{day:.2f}%/{(day5 or 0):.2f}%/{(day20 or 0):.2f}%，换手率{turnover if turnover is not None else '缺失'}，PE(TTM){pe if pe is not None else '缺失'}。",
            "risk": "动量可能反转；估值口径、公告与最新财报仍需逐项复核。",
        })
    ranked = sorted(rows, key=lambda item: item["score"], reverse=True)[:limit]
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
        item["buy_percentage"] = int(round(max(5, min(95, item["score"]))))
        item["sell_percentage"] = 100 - item["buy_percentage"]
        item["recommendation"] = "建议买入" if item["buy_percentage"] >= 65 else ("建议卖出" if item["sell_percentage"] >= 65 else "建议持有观察")
    return {
        "source": "AKShare stock_zh_a_spot_tx + transparent local scoring", "as_of": str(date.today()),
        "ranking": ranked,
        "method": "评分=clamp(50+1日涨幅×1.2+5日涨幅×0.8+20日涨幅×0.35+流动性分+估值分,0,100)；买入倾向=clamp(评分,5,95)，卖出倾向=100-买入倾向；剔除ST和接近涨停标的。",
        "note": "排名按买入倾向降序；百分比不是建议仓位或收益预测。",
    }


def history(symbol: str, start: str = "", end: str = "", adjust: str = "qfq") -> pd.DataFrame:
    today = date.today()
    start_date = (pd.Timestamp(start).date() if start else today - timedelta(days=730)).strftime("%Y%m%d")
    end_date = (pd.Timestamp(end).date() if end else today).strftime("%Y%m%d")
    source = "AKShare stock_zh_a_hist"
    try:
        frame = ak.stock_zh_a_hist(
            symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust=adjust or "", timeout=15,
        )
    except Exception as primary_error:
        if symbol.startswith(("4", "8")):
            raise primary_error
        exchange_symbol = ("sh" if symbol.startswith(("5", "6", "9")) else "sz") + symbol
        frame = ak.stock_zh_a_daily(symbol=exchange_symbol, start_date=start_date, end_date=end_date, adjust=adjust or "")
        source = "AKShare stock_zh_a_daily (Sina fallback)"
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
    frame = frame.sort_values("date").reset_index(drop=True)
    frame.attrs["source"] = source
    return frame


def bars_response(symbol: str, start: str, end: str, adjust: str):
    frame = history(symbol, start, end, adjust)
    fields = [field for field in ["date", "open", "high", "low", "close", "volume", "amount", "turnover"] if field in frame]
    bars = []
    for row in frame[fields].to_dict("records"):
        row["date"] = iso_date(row["date"])
        bars.append(clean_record(row))
    return {
        "symbol": symbol, "source": frame.attrs.get("source", "AKShare A-share history"), "as_of": bars[-1]["date"],
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
        "symbol": symbol, "source": frame.attrs.get("source", "AKShare A-share history") + " + local calculation", "as_of": iso_date(last["date"]),
        "adjust": adjust or "none", "lookback_trading_days": len(frame), "latest": latest,
        "annualized_volatility": to_number(returns.std() * math.sqrt(252)),
        "max_drawdown": to_number(drawdown.min()),
        "method_note": "RSI uses a 14-day simple rolling average; MACD uses EMA(12,26,9).",
    }


def spot_row(symbol: str) -> dict:
    try:
        frame = ak.stock_zh_a_spot_em()
        row = frame.loc[frame["代码"].astype(str).str.zfill(6) == symbol]
        if not row.empty:
            result = clean_record(row.iloc[0].to_dict())
            result["_source"] = "AKShare stock_zh_a_spot_em"
            return result
    except Exception:
        pass
    frame = ak.stock_zh_a_spot_tx()
    digits = frame["code"].astype(str).str.replace(r"\D", "", regex=True).str[-6:]
    row = frame.loc[digits == symbol]
    if row.empty:
        raise ValueError("未找到该代码的行情快照。")
    raw = clean_record(row.iloc[0].to_dict())
    result = dict(raw)
    result.update({
        "代码": symbol, "名称": raw.get("name"), "最新价": raw.get("zxj"), "涨跌幅": raw.get("zdf"),
        "成交量": raw.get("volume"), "成交额": raw.get("turnover"), "换手率": raw.get("hsl"),
        "市盈率-动态": raw.get("pe_ttm"), "总市值": raw.get("zsz"),
        "_source": "AKShare stock_zh_a_spot_tx (Tencent fallback)",
    })
    return result


def quote_response(symbol: str):
    row = spot_row(symbol)
    source = row.pop("_source", "AKShare A-share spot")
    return {
        "symbol": symbol, "source": source, "as_of": str(row.get("更新时间") or date.today()),
        "note": "数据源快照，不是券商逐笔实时行情。", "quote": row,
    }


def valuation_response(symbol: str):
    row = spot_row(symbol)
    source = row.pop("_source", "AKShare A-share spot")
    keys = ["代码", "名称", "最新价", "市盈率-动态", "市盈率-静态", "市净率", "总市值", "流通市值", "涨跌幅"]
    return {
        "symbol": symbol, "source": source, "as_of": str(row.get("更新时间") or date.today()),
        "valuation": {key: row.get(key) for key in keys if key in row},
        "note": "PE/PB口径由上游数据源决定；请在正式决策前复核口径和时点。",
    }


def fundamentals_response(symbol: str):
    frame = ak.stock_financial_analysis_indicator(symbol=symbol)
    if frame.empty:
        raise ValueError("数据源未返回财务分析指标。")
    report_key = next((key for key in ["日期", "报告期", "报告日期"] if key in frame.columns), None)
    if report_key:
        frame = frame.assign(_report_sort=pd.to_datetime(frame[report_key], errors="coerce")).sort_values("_report_sort", ascending=False).drop(columns=["_report_sort"])
    frame = frame.head(8).copy()
    records = [clean_record(item) for item in frame.to_dict("records")]
    latest_period = str(records[0].get(report_key)) if records and report_key else None
    return {
        "symbol": symbol, "source": "AKShare stock_financial_analysis_indicator", "as_of": latest_period or str(date.today()),
        "latest_report_period": latest_period,
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


def analyze_stock_response(symbol: str, announcement_days: int = 180):
    unavailable = []

    def attempt(label, function):
        try:
            return function()
        except Exception as exc:
            unavailable.append(f"{label}不可用：{exc}")
            return {}

    quote = attempt("行情", lambda: quote_response(symbol))
    indicators = attempt("技术指标", lambda: indicators_response(symbol, 260, "qfq"))
    fundamentals = attempt("财务", lambda: fundamentals_response(symbol))
    valuation = {}
    start = str(date.today() - timedelta(days=announcement_days - 1))
    announcements = attempt("公告", lambda: announcements_response(symbol, start, str(date.today()), 30))

    quote_row = quote.get("quote", {})
    if quote_row:
        valuation = {
            "symbol": symbol, "source": quote.get("source"), "as_of": quote.get("as_of"),
            "valuation": {key: quote_row.get(key) for key in ["代码", "名称", "最新价", "市盈率-动态", "市盈率-静态", "市净率", "总市值", "流通市值", "涨跌幅"] if key in quote_row},
        }
    else:
        valuation = attempt("估值", lambda: valuation_response(symbol))
    fallback_name = None
    if not quote_row:
        search = attempt("股票名称", lambda: stock_search_response(symbol, 1))
        if search.get("matches"):
            fallback_name = search["matches"][0].get("name")
    latest = indicators.get("latest", {})
    close = safe_float(latest.get("close") or first_value(quote_row, "最新价", "price"))
    ma20, ma60, rsi = (safe_float(latest.get(key)) for key in ("ma20", "ma60", "rsi14"))
    short_points = []
    short_score = 0
    if close is not None and ma20 is not None:
        above = close >= ma20
        short_score += 1 if above else -1
        short_points.append(f"收盘价{'高于' if above else '低于'}MA20")
    if close is not None and ma60 is not None:
        above = close >= ma60
        short_score += 1 if above else -1
        short_points.append(f"收盘价{'高于' if above else '低于'}MA60")
    if rsi is not None:
        if rsi >= 70:
            short_score -= 1
            short_points.append("RSI处于偏热区间")
        elif rsi <= 30:
            short_points.append("RSI处于偏弱区间，反转尚待确认")
        else:
            short_points.append("RSI未处于极端区间")
    short_label = "短期继续跟踪" if short_score >= 2 else ("短期谨慎观察" if short_score <= -1 else "短期等待确认")

    valuation_row = valuation.get("valuation", {})
    pe = safe_float(first_value(valuation_row, "市盈率-动态", "市盈率-静态", "pe"))
    pb = safe_float(first_value(valuation_row, "市净率", "pb"))
    latest_financial = fundamentals.get("records", [{}])[0] if fundamentals.get("records") else {}
    roe = safe_float(first_value(latest_financial, "净资产收益率", "ROE"))
    revenue_growth = safe_float(first_value(latest_financial, "营业收入增长率", "营收增长"))
    profit_growth = safe_float(first_value(latest_financial, "净利润增长率", "净利润同比增长"))
    long_points = []
    long_score = 0
    if pe is not None:
        long_points.append(f"PE约{pe:.2f}")
        long_score += 1 if 0 < pe <= 40 else (-1 if pe <= 0 or pe > 80 else 0)
    if pb is not None:
        long_points.append(f"PB约{pb:.2f}")
    if roe is not None:
        long_points.append(f"最新报告期ROE约{roe:.2f}")
        long_score += 1 if roe >= 10 else (-1 if roe < 3 else 0)
    for label, value in (("营收增速", revenue_growth), ("净利润增速", profit_growth)):
        if value is not None:
            long_points.append(f"{label}约{value:.2f}%")
            long_score += 1 if value > 0 else -1
    if not latest_financial:
        long_points.append("财务数据待补充")
    long_label = "长期进入观察池" if long_score >= 2 else ("长期谨慎复核" if long_score <= -1 else "长期继续观察")
    signal_count = sum(value is not None for value in [close, ma20, ma60, rsi, pe, roe, revenue_growth, profit_growth])
    directional_score = short_score * 7 + long_score * 6
    buy_percentage = int(round(max(5, min(95, 50 + directional_score)))) if signal_count else 50
    sell_percentage = 100 - buy_percentage
    if signal_count < 3:
        trade_conclusion = "信息不足，暂缓决策"
    elif buy_percentage >= 65:
        trade_conclusion = "建议买入"
    elif sell_percentage >= 65:
        trade_conclusion = "建议卖出"
    else:
        trade_conclusion = "建议持有观察"
    confidence = min(90, 20 + signal_count * 9)
    return {
        "symbol": symbol, "name": first_value(quote_row, "名称", "name") or fallback_name,
        "as_of": indicators.get("as_of") or quote.get("as_of") or fundamentals.get("as_of"),
        "short_term": {"horizon": "未来1–4周", "action": short_label, "evidence": short_points or ["技术数据不足"]},
        "long_term": {"horizon": "未来6–24个月", "action": long_label, "evidence": long_points},
        "recommendation": {
            "conclusion": trade_conclusion, "buy_percentage": buy_percentage, "sell_percentage": sell_percentage,
            "confidence": confidence,
            "interpretation": "买入/卖出百分比是证据方向倾向，不是建议仓位，也不是上涨/下跌概率。",
            "method": "买入倾向=clamp(50+短期技术分×7+长期基本面估值分×6,5,95)；买入≥65为建议买入，卖出≥65为建议卖出，否则持有观察；有效信号少于3项则暂缓决策。",
            "score_components": {"short_technical_score": short_score, "long_fundamental_valuation_score": long_score, "valid_signal_count": signal_count},
            "basis": (short_points + long_points)[:6],
        },
        "latest_report_period": fundamentals.get("latest_report_period"),
        "financial_history": fundamentals.get("records", []),
        "recent_announcements": announcements.get("announcements", []),
        "quote": quote, "indicators": indicators, "valuation": valuation,
        "unavailable": unavailable,
        "risk_note": "买卖结论基于历史数据和公开披露，可能随价格、财报或公告变化；需结合行业周期、公告原文与个人风险承受能力复核。",
    }


def markdown_cell(value):
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def daily_report_response(candidate_limit: int = 5):
    portfolio = portfolio_response()
    if not portfolio["holdings"]:
        return {
            "as_of": str(date.today()), "is_trading_day": None, "has_holdings": False,
            "empty_reason": "no_holdings", "markdown": "",
            "note": "未保存持仓，不生成日报。请先在 DSH Web 中调用 set_portfolio 保存持仓。",
        }
    calendar = trading_day_response()
    if not calendar["is_trading_day"]:
        return {
            "as_of": str(date.today()), "is_trading_day": False, "has_holdings": True,
            "empty_reason": "non_trading_day", "markdown": "", "source": calendar["source"],
            "note": "今日不是交易日，不生成日报。",
        }
    def analyze_holding(holding):
        analysis = analyze_stock_response(holding["symbol"], 7)
        analysis["holding"] = holding
        return analysis

    worker_count = min(8, max(1, len(portfolio["holdings"])))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        holdings = list(executor.map(analyze_holding, portfolio["holdings"]))
    report_warnings = []
    try:
        weekly = market_events_response(7, 20)
        monthly = market_events_response(30, 40)
    except Exception as exc:
        report_warnings.append(f"市场事件不可用：{exc}")
        weekly = {"events": []}
        monthly = {"events": []}
    try:
        candidates = candidate_ranking_response(candidate_limit)
    except Exception as exc:
        report_warnings.append(f"候选排行不可用：{exc}")
        candidates = {"ranking": [], "method": "当日候选数据不可用。"}
    lines = [
        "# A股开盘前研究简报", "", f"数据日期：{date.today()}（08:55开始汇总，目标于09:00开盘前30分钟送达）", "",
        "## 已持仓股票", "", "| 代码 | 名称 | 最新数据日 | 短期（1–4周） | 长期（6–24个月） | 买入倾向 | 卖出倾向 | 总结 | 财报期 | 风险/缺失 |",
        "|---|---|---|---|---|---:|---:|---|---|---|",
    ]
    if not holdings:
        lines.append("| — | 尚未保存持仓 | — | — | — | — | — | — | — | 请先调用 set_portfolio |")
    for item in holdings:
        lines.append("| " + " | ".join(markdown_cell(value) for value in [
            item["symbol"], item.get("name") or item["holding"].get("name"), item.get("as_of"),
            item["short_term"]["action"] + "；" + "、".join(item["short_term"]["evidence"][:2]),
            item["long_term"]["action"] + "；" + "、".join(item["long_term"]["evidence"][:2]),
            f"{item['recommendation']['buy_percentage']}%", f"{item['recommendation']['sell_percentage']}%",
            item["recommendation"]["conclusion"],
            item.get("latest_report_period"), "；".join(item["unavailable"]) or item["risk_note"],
        ]) + " |")

    lines.extend(["", "## 最近一周重要事件线索", ""])
    lines.extend([f"- {event.get('date') or '日期待核验'}【{event.get('theme', '综合事件')}】{event['title']}；可能路径：{event.get('possible_impact_path', '待核验')}" for event in weekly["events"][:10]] or ["- 暂无可得事件。"])
    weekly_titles = {event["title"] for event in weekly["events"]}
    month_only = [event for event in monthly["events"] if event["title"] not in weekly_titles]
    lines.extend(["", "## 最近一月其他重要事件线索", ""])
    lines.extend([f"- {event.get('date') or '日期待核验'}【{event.get('theme', '综合事件')}】{event['title']}；可能路径：{event.get('possible_impact_path', '待核验')}" for event in month_only[:10]] or ["- 当前本地90日滚动档案尚无一周窗口之外的事件。"])
    lines.extend(["", "## 热门股票推荐排行", "", "| 排名 | 代码 | 名称 | 评分 | 买入倾向 | 卖出倾向 | 1日 | 5日 | 20日 | PE(TTM) | 买卖建议 | 推荐理由与风险 |", "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"])
    for item in candidates["ranking"]:
        lines.append("| " + " | ".join(markdown_cell(value) for value in [
            item.get("rank"), item.get("symbol"), item.get("name"), item.get("score"),
            f"{item['buy_percentage']}%", f"{item['sell_percentage']}%", item.get("pct_change_1d"), item.get("pct_change_5d"),
            item.get("pct_change_20d"), item.get("pe_ttm"), item.get("recommendation"),
        ]) + " | " + markdown_cell(item.get("reason", "") + item.get("risk", "")) + " |")
    lines.extend(["", f"评分方法：{candidates['method']}", "", "说明：买入/卖出百分比表示当前证据的方向倾向，两者合计100%；不是仓位比例或涨跌概率。结论可能随新行情、财报和公告变化。"])
    if report_warnings:
        lines.extend(["", "## 数据缺失", ""] + [f"- {warning}" for warning in report_warnings])
    return {
        "as_of": str(date.today()), "is_trading_day": True, "has_holdings": True, "portfolio": holdings,
        "weekly_events": weekly, "monthly_events": monthly, "candidates": candidates,
        "warnings": report_warnings, "markdown": "\n".join(lines),
    }


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
            if parsed.path == "/v1/search":
                limit = min(max(int(value("limit", "10")), 1), 50)
                return self.send_json(200, stock_search_response(value("query"), limit))
            if parsed.path == "/v1/trading-day":
                return self.send_json(200, trading_day_response(value("date")))
            if parsed.path == "/v1/market-brief":
                limit = min(max(int(value("event_limit", "20")), 1), 50)
                return self.send_json(200, market_brief_response(limit))
            if parsed.path == "/v1/market-events":
                days = min(max(int(value("days", "7")), 1), 90)
                limit = min(max(int(value("limit", "20")), 1), 100)
                return self.send_json(200, market_events_response(days, limit))
            if parsed.path == "/v1/market-movers":
                limit = min(max(int(value("limit", "20")), 1), 50)
                return self.send_json(200, market_movers_response(limit))
            if parsed.path == "/v1/candidate-ranking":
                limit = min(max(int(value("limit", "5")), 1), 20)
                return self.send_json(200, candidate_ranking_response(limit))
            if parsed.path == "/v1/daily-report":
                limit = min(max(int(value("candidate_limit", "5")), 1), 20)
                return self.send_json(200, daily_report_response(limit))
            symbol = value("symbol")
            if not symbol.isdigit() or len(symbol) != 6:
                raise ValueError("symbol 必须是6位A股代码。")
            if parsed.path == "/v1/quote": payload = quote_response(symbol)
            elif parsed.path == "/v1/ohlcv": payload = bars_response(symbol, value("start"), value("end"), value("adjust", "qfq"))
            elif parsed.path == "/v1/indicators": payload = indicators_response(symbol, int(value("lookback", "260")), value("adjust", "qfq"))
            elif parsed.path == "/v1/fundamentals": payload = fundamentals_response(symbol)
            elif parsed.path == "/v1/announcements": payload = announcements_response(symbol, value("start"), value("end"), min(max(int(value("limit", "20")), 1), 100))
            elif parsed.path == "/v1/valuation": payload = valuation_response(symbol)
            elif parsed.path == "/v1/analysis": payload = analyze_stock_response(symbol, min(max(int(value("announcement_days", "180")), 1), 730))
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
