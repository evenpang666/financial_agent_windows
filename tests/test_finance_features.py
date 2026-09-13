import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


class FakeAkshare(types.ModuleType):
    __version__ = "test"

    def stock_info_a_code_name(self):
        return pd.DataFrame([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000858", "name": "五粮液"},
            {"code": "600809", "name": "山西汾酒"},
        ])

    def tool_trade_date_hist_sina(self):
        return pd.DataFrame({"trade_date": ["2026-09-11", "2026-09-14"]})

    def stock_zh_a_spot_tx(self):
        return pd.DataFrame([
            {"code": "600519", "name": "贵州茅台", "zxj": 1500, "zdf": 2, "zdf_d5": 4, "zdf_d20": 8, "hsl": 2, "pe_ttm": 25, "zsz": 100},
            {"code": "000858", "name": "五粮液", "zxj": 120, "zdf": 1, "zdf_d5": 2, "zdf_d20": 3, "hsl": 3, "pe_ttm": 20, "zsz": 90},
            {"code": "000001", "name": "ST测试", "zxj": 5, "zdf": 8, "zdf_d5": 10, "zdf_d20": 20, "hsl": 5, "pe_ttm": 10, "zsz": 10},
        ])


sys.modules["akshare"] = FakeAkshare("akshare")
spec = importlib.util.spec_from_file_location("stock_data_server", ROOT / "scripts" / "stock_data_server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class FinanceFeatureTests(unittest.TestCase):
    def test_stock_search_prefers_exact_symbol(self):
        result = server.stock_search_response("600519", 10)
        self.assertEqual(result["matches"][0], {"symbol": "600519", "name": "贵州茅台"})

    def test_trading_calendar(self):
        self.assertTrue(server.trading_day_response("2026-09-14")["is_trading_day"])
        self.assertFalse(server.trading_day_response("2026-09-13")["is_trading_day"])

    def test_candidate_ranking_is_ranked_and_excludes_st(self):
        result = server.candidate_ranking_response(5)
        self.assertEqual([item["symbol"] for item in result["ranking"]], ["600519", "000858"])
        self.assertEqual(result["ranking"][0]["rank"], 1)
        self.assertEqual(result["ranking"][0]["buy_percentage"] + result["ranking"][0]["sell_percentage"], 100)
        self.assertIn(result["ranking"][0]["recommendation"], {"建议买入", "建议持有观察", "建议卖出"})

    def test_analysis_has_short_and_long_horizons(self):
        with patch.object(server, "quote_response", return_value={"as_of": "2026-09-11", "quote": {"名称": "贵州茅台"}}), \
             patch.object(server, "indicators_response", return_value={"as_of": "2026-09-11", "latest": {"close": 110, "ma20": 100, "ma60": 90, "rsi14": 60}}), \
             patch.object(server, "fundamentals_response", return_value={"latest_report_period": "2026-06-30", "records": [{"净资产收益率": 15, "营业收入增长率": 8, "净利润增长率": 9}]}), \
             patch.object(server, "valuation_response", return_value={"valuation": {"市盈率-动态": 25, "市净率": 4}}), \
             patch.object(server, "announcements_response", return_value={"announcements": []}):
            result = server.analyze_stock_response("600519")
        self.assertEqual(result["short_term"]["horizon"], "未来1–4周")
        self.assertEqual(result["long_term"]["horizon"], "未来6–24个月")
        self.assertEqual(result["latest_report_period"], "2026-06-30")
        self.assertEqual(result["recommendation"]["buy_percentage"] + result["recommendation"]["sell_percentage"], 100)
        self.assertEqual(result["recommendation"]["conclusion"], "建议买入")

    def test_daily_report_contains_required_tables_and_windows(self):
        analysis = {
            "symbol": "600519", "name": "贵州茅台", "as_of": "2026-09-11",
            "short_term": {"action": "短期继续跟踪", "evidence": ["价格高于MA20"]},
            "long_term": {"action": "长期进入观察池", "evidence": ["ROE稳定"]},
            "recommendation": {"buy_percentage": 70, "sell_percentage": 30, "conclusion": "建议买入"},
            "latest_report_period": "2026-06-30", "unavailable": [], "risk_note": "估值波动",
        }
        ranking = {"ranking": [{"rank": 1, "symbol": "000858", "name": "五粮液", "score": 80, "buy_percentage": 80, "sell_percentage": 20, "recommendation": "建议买入", "pct_change_1d": 1, "pct_change_5d": 2, "pct_change_20d": 3, "pe_ttm": 20}], "method": "测试评分"}
        events = {"events": [{"date": "2026-09-11", "title": "测试事件"}]}
        with patch.object(server, "trading_day_response", return_value={"is_trading_day": True, "source": "test"}), \
             patch.object(server, "portfolio_response", return_value={"holdings": [{"symbol": "600519"}]}), \
             patch.object(server, "analyze_stock_response", return_value=analysis.copy()), \
             patch.object(server, "market_events_response", return_value=events), \
             patch.object(server, "candidate_ranking_response", return_value=ranking):
            result = server.daily_report_response(5)
        self.assertIn("短期（1–4周）", result["markdown"])
        self.assertIn("最近一月", result["markdown"])
        self.assertIn("热门股票推荐排行", result["markdown"])
        self.assertIn("买入倾向", result["markdown"])
        self.assertIn("卖出倾向", result["markdown"])


if __name__ == "__main__":
    unittest.main()
