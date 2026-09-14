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
    def test_daily_report_without_holdings_keeps_panorama_and_candidates(self):
        panorama = {
            "market_state": {"summary": "中性市场", "risk_level": "中", "reference_position": "40%–60%", "buy_tendency_adjustment": 0},
            "market_breadth": {"label": "均衡", "advancing": 2000, "declining": 2000, "score": 50},
            "social_sentiment": {"label": "中性", "score": 50},
            "international_events": [], "domestic_policies": [], "unavailable": [],
        }
        ranking = {"ranking": [], "method": "测试评分"}
        with patch.object(server, "portfolio_response", return_value={"holdings": []}), \
             patch.object(server, "trading_day_response", return_value={"is_trading_day": True, "source": "test"}), \
             patch.object(server, "market_panorama_response", return_value=panorama), \
             patch.object(server, "market_events_response", return_value={"events": []}), \
             patch.object(server, "candidate_ranking_response", return_value=ranking), \
             patch.object(server, "analyze_stock_response") as analyze:
            result = server.daily_report_response(5)
        self.assertTrue(result["is_trading_day"])
        self.assertFalse(result["has_holdings"])
        self.assertIn("市场全景", result["markdown"])
        self.assertIn("热门股票推荐排行", result["markdown"])
        self.assertNotIn("已持仓股票", result["markdown"])
        analyze.assert_not_called()

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

    def test_market_breadth_and_risk_calibration_are_auditable(self):
        frame = pd.DataFrame([
            {"zdf": -2, "zdf_d5": -4, "zdf_d20": -8},
            {"zdf": -1, "zdf_d5": -2, "zdf_d20": -3},
            {"zdf": 0, "zdf_d5": -1, "zdf_d20": -2},
            {"zdf": 1, "zdf_d5": 1, "zdf_d20": -1},
        ])
        breadth = server.market_breadth_response(frame)
        self.assertEqual(breadth["advancing"], 1)
        self.assertEqual(breadth["declining"], 2)
        self.assertLess(breadth["score"], 50)
        calibrated = server.calibrate_buy_sell(70, {"market_state": {"buy_tendency_adjustment": -12}})
        self.assertEqual(calibrated["base_buy_percentage"], 70)
        self.assertEqual(calibrated["buy_percentage"], 58)
        self.assertEqual(calibrated["buy_percentage"] + calibrated["sell_percentage"], 100)

    def test_market_panorama_splits_events_and_sets_risk_position(self):
        events = {"events": [
            {"date": "2026-09-14", "title": "美联储加息并提示衰退风险"},
            {"date": "2026-09-14", "title": "国务院印发支持消费政策"},
        ]}
        breadth = {"score": 20, "label": "弱势扩散", "advancing": 100, "declining": 400, "limit_up_count": 2, "limit_down_count": 20}
        sentiment = {"score": 25, "label": "偏谨慎", "hot_stocks": [], "unavailable": []}
        with patch.object(server, "market_breadth_response", return_value=breadth), \
             patch.object(server, "social_sentiment_response", return_value=sentiment), \
             patch.object(server, "market_events_response", return_value=events):
            result = server.market_panorama_response()
        self.assertEqual(len(result["international_events"]), 1)
        self.assertEqual(len(result["domestic_policies"]), 1)
        self.assertEqual(result["market_state"]["risk_level"], "极高")
        self.assertEqual(result["market_state"]["reference_position"], "0%–20%")
        self.assertEqual(result["market_state"]["buy_tendency_adjustment"], -20)

    def test_market_context_exposes_partial_coverage(self):
        panorama = {
            "as_of": "2026-09-14", "source": "test", "event_window": {"days": 7, "start": "2026-09-08", "end": "2026-09-14"},
            "international_events": [], "domestic_policies": [],
            "market_breadth": {"as_of": "2026-09-14", "score": 50},
            "social_sentiment": {"as_of": "2026-09-14", "hot_stocks": [], "note": "价格代理"},
            "unavailable": ["事件与政策不可用：timeout", "东方财富人气榜不可用：timeout"],
        }
        context = server.market_context_summary(panorama)
        self.assertFalse(context["complete"])
        self.assertEqual(context["coverage"]["international_events"]["status"], "unavailable")
        self.assertEqual(context["coverage"]["domestic_policies"]["status"], "unavailable")
        self.assertEqual(context["coverage"]["social_attention"]["status"], "proxy_only")
        self.assertEqual(context["event_window"]["end"], "2026-09-14")

    def test_related_enterprises_selects_industry_leaders_and_loads_announcements(self):
        info = pd.DataFrame([{"item": "股票简称", "value": "贵州茅台"}, {"item": "行业", "value": "酿酒行业"}])
        constituents = pd.DataFrame([
            {"代码": "600519", "名称": "贵州茅台"}, {"代码": "000858", "名称": "五粮液"}, {"代码": "600809", "名称": "山西汾酒"},
        ])
        spot = pd.DataFrame([
            {"代码": "600519", "名称": "贵州茅台", "总市值": 2000},
            {"代码": "000858", "名称": "五粮液", "总市值": 1000},
            {"代码": "600809", "名称": "山西汾酒", "总市值": 800},
        ])
        announcement = {"as_of": "2026-09-14", "announcements": [{"公告标题": "半年报"}]}
        with patch.object(server.ak, "stock_individual_info_em", create=True, return_value=info), \
             patch.object(server.ak, "stock_board_industry_cons_em", create=True, return_value=constituents), \
             patch.object(server.ak, "stock_zh_a_spot_em", create=True, return_value=spot), \
             patch.object(server, "announcements_response", return_value=announcement) as announcements:
            result = server.related_enterprises_response("600519", 30, 2)
        self.assertEqual(result["industry"], "酿酒行业")
        self.assertEqual([item["symbol"] for item in result["enterprises"]], ["000858", "600809"])
        self.assertEqual(result["enterprises"][0]["announcements"][0]["公告标题"], "半年报")
        self.assertEqual(announcements.call_count, 2)

    def test_analysis_has_short_and_long_horizons(self):
        with patch.object(server, "quote_response", return_value={"as_of": "2026-09-11", "quote": {"名称": "贵州茅台"}}), \
             patch.object(server, "indicators_response", return_value={"as_of": "2026-09-11", "latest": {"close": 110, "ma20": 100, "ma60": 90, "rsi14": 60}}), \
             patch.object(server, "fundamentals_response", return_value={"latest_report_period": "2026-06-30", "records": [{"净资产收益率": 15, "营业收入增长率": 8, "净利润增长率": 9}]}), \
             patch.object(server, "valuation_response", return_value={"valuation": {"市盈率-动态": 25, "市净率": 4}}), \
             patch.object(server, "announcements_response", return_value={"announcements": []}):
            result = server.analyze_stock_response("600519", panorama={
                "as_of": "2026-09-14", "market_state": {"risk_level": "高", "buy_tendency_adjustment": -12},
                "unavailable": ["事件与政策不可用：timeout"],
            })
        self.assertEqual(result["short_term"]["horizon"], "未来1–4周")
        self.assertEqual(result["long_term"]["horizon"], "未来6–24个月")
        self.assertEqual(result["latest_report_period"], "2026-06-30")
        self.assertEqual(result["recommendation"]["buy_percentage"] + result["recommendation"]["sell_percentage"], 100)
        self.assertEqual(result["recommendation"]["score_components"]["base_buy_percentage"], 82)
        self.assertEqual(result["recommendation"]["buy_percentage"], 70)
        self.assertEqual(result["recommendation"]["score_components"]["market_adjustment"], -12)
        self.assertEqual(result["recommendation"]["conclusion"], "建议买入")
        self.assertFalse(result["market_context"]["complete"])
        self.assertTrue(any("市场全景：事件与政策不可用" in item for item in result["unavailable"]))

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
        panorama = {
            "market_state": {"summary": "测试市场状态", "risk_level": "高", "reference_position": "20%–40%", "buy_tendency_adjustment": -12},
            "market_breadth": {"label": "偏弱", "advancing": 1000, "declining": 3000, "score": 30},
            "social_sentiment": {"label": "偏谨慎", "score": 35},
            "international_events": [{"date": "2026-09-11", "title": "测试国际事件", "tone": "negative"}],
            "domestic_policies": [{"date": "2026-09-11", "title": "测试国内政策", "tone": "positive"}],
            "unavailable": [],
        }
        with patch.object(server, "trading_day_response", return_value={"is_trading_day": True, "source": "test"}), \
             patch.object(server, "portfolio_response", return_value={"holdings": [{"symbol": "600519"}]}), \
             patch.object(server, "market_panorama_response", return_value=panorama), \
             patch.object(server, "analyze_stock_response", return_value=analysis.copy()), \
             patch.object(server, "market_events_response", return_value=events), \
             patch.object(server, "candidate_ranking_response", return_value=ranking):
            result = server.daily_report_response(5)
        self.assertIn("短期（1–4周）", result["markdown"])
        self.assertIn("最近一月", result["markdown"])
        self.assertIn("热门股票推荐排行", result["markdown"])
        self.assertIn("买入倾向", result["markdown"])
        self.assertIn("卖出倾向", result["markdown"])
        self.assertIn("市场全景", result["markdown"])
        self.assertIn("国际事件", result["markdown"])
        self.assertIn("国内政策", result["markdown"])
        self.assertEqual(result["market_panorama"]["market_state"]["risk_level"], "高")


if __name__ == "__main__":
    unittest.main()
