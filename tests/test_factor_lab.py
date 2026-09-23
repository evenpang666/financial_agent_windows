import importlib.util
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("factor_lab", ROOT / "scripts" / "factor_lab.py")
factor_lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factor_lab)


class FactorLabTests(unittest.TestCase):
    def tearDown(self):
        factor_lab.EM_MINUTE_UNAVAILABLE.clear()
        factor_lab.SINA_MINUTE_UNAVAILABLE.clear()

    def test_spot_snapshot_falls_back_to_sina_and_normalizes_codes(self):
        rows = pd.DataFrame([{"代码": "sh600519", "名称": "贵州茅台", "最新价": 100,
                              "今开": 99, "昨收": 98, "最高": 101, "最低": 97,
                              "成交额": 100000, "成交量": 1000}])
        fake_ak = types.SimpleNamespace(stock_zh_a_spot_em=lambda: (_ for _ in ()).throw(ConnectionError("Eastmoney offline")),
                                        stock_zh_a_spot=lambda: rows)
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            result = factor_lab.spot_frame()
        self.assertEqual(result.iloc[0]["代码"], "600519")
        self.assertIn("新浪", result.attrs["source"])

    def test_spot_snapshot_reports_both_source_failures(self):
        def fail_primary():
            raise ConnectionError("Eastmoney HTTPSConnectionPool")
        def fail_fallback():
            raise ConnectionError("Sina timeout")
        fake_ak = types.SimpleNamespace(stock_zh_a_spot_em=fail_primary, stock_zh_a_spot=fail_fallback)
        with patch.dict(sys.modules, {"akshare": fake_ak}), self.assertRaisesRegex(RuntimeError, "新浪数据源均不可用") as caught:
            factor_lab.spot_frame()
        self.assertIn("Sina timeout", str(caught.exception))

    def test_minute_history_falls_back_once_and_normalizes_sina_fields(self):
        calls = []
        def eastmoney(**kwargs):
            calls.append(kwargs["symbol"])
            raise ConnectionError("HTTPSConnectionPool(host='push2his.eastmoney.com')")
        sina_rows = pd.DataFrame([{"day": "2026-09-22 09:35:00", "open": 100, "high": 101,
                                   "low": 99, "close": 100, "volume": 1000, "amount": 100000}])
        fake_ak = types.SimpleNamespace(stock_zh_a_hist_min_em=eastmoney)
        start, end = datetime(2026, 9, 20), datetime(2026, 9, 23)
        with patch.dict(sys.modules, {"akshare": fake_ak}), patch.object(factor_lab, "sina_minute_frame", return_value=sina_rows), patch.object(factor_lab.time, "sleep"):
            first = factor_lab.fetch_bars("600519", start, end)
            second = factor_lab.fetch_bars("600519", start, end)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(first), 1)
        self.assertEqual(first.iloc[0]["close"], 100)
        self.assertEqual(len(second), 1)

    def test_sina_jsonp_has_timeout_and_parses_ohlcv(self):
        response = types.SimpleNamespace(text='callback=([{"day":"2026-09-22 09:35:00","open":"100","high":"101","low":"99","close":"100","volume":"1000","amount":"100000"}]);',
                                         raise_for_status=lambda: None)
        with patch.object(factor_lab.requests, "get", return_value=response) as get:
            frame = factor_lab.sina_minute_frame("sh600519")
        self.assertEqual(frame.iloc[0]["day"], "2026-09-22 09:35:00")
        self.assertEqual(get.call_args.kwargs["timeout"], 15)

    def test_network_failure_disables_repeated_sina_minute_requests(self):
        fake_ak = types.SimpleNamespace(stock_zh_a_hist_min_em=lambda **kwargs: (_ for _ in ()).throw(ConnectionError("Eastmoney down")))
        start, end = datetime(2026, 9, 20), datetime(2026, 9, 23)
        with patch.dict(sys.modules, {"akshare": fake_ak}), \
             patch.object(factor_lab, "sina_minute_frame", side_effect=factor_lab.requests.ConnectionError("Sina down")) as sina:
            with self.assertRaisesRegex(RuntimeError, "均不可用"):
                factor_lab.fetch_bars("600519", start, end)
            with self.assertRaisesRegex(RuntimeError, "已停止重复请求"):
                factor_lab.fetch_bars("600520", start, end)
        self.assertEqual(sina.call_count, 1)

    def test_universe_falls_back_to_market_cap_before_open(self):
        rows = [{"代码": f"600{index:03d}", "名称": f"股票{index}", "成交额": 0,
                 "总市值": index + 1} for index in range(45)]
        universe = factor_lab.choose_universe(pd.DataFrame(rows))
        self.assertEqual(len(universe), factor_lab.UNIVERSE_SIZE)
        self.assertEqual(universe[0]["symbol"], "600044")

    def test_history_labels_only_use_same_day_future_close(self):
        rows = []
        for day, opening, closing in (("2026-09-21", 100, 110), ("2026-09-22", 110, 120)):
            for index in range(48):
                hour = 9 + (35 + index * 5) // 60
                minute = (35 + index * 5) % 60
                if index >= 24:
                    hour, minute = 13 + ((index - 24) * 5) // 60, ((index - 24) * 5) % 60
                moment = f"{day} {hour:02d}:{minute:02d}:00"
                price = closing if index == 47 else opening + index * 0.05
                rows.append({"time": moment, "open": opening, "high": price + 1, "low": opening - 1,
                             "close": price, "volume": 100, "amount": price * 10000})
        bars = pd.DataFrame(rows)
        bars["time"] = pd.to_datetime(bars["time"])
        samples = factor_lab.history_samples("600001", bars)
        self.assertTrue(samples)
        self.assertTrue(all(item["day"] == "2026-09-22" for item in samples))
        self.assertTrue(all(item["target"] > 0 for item in samples))

    def test_evaluation_uses_chronological_validation_and_separate_holdout(self):
        samples = []
        for day in pd.bdate_range("2026-08-01", periods=20):
            for slot in factor_lab.SLOTS:
                for stock in range(20):
                    x = (stock - 10) / 100
                    samples.append({"symbol": f"600{stock:03d}", "day": day.strftime("%Y-%m-%d"),
                                    "slot": slot, "target": x / 10, "open_momentum": x,
                                    "gap": x, "range_position": x, "range_width": x,
                                    "vwap_gap": x})
        result = factor_lab.evaluate(samples, [], [])
        self.assertEqual(result["train_days"] + result["validation_days"] + result["holdout_days"], 20)
        self.assertEqual(result["first_day"], "2026-08-03")
        self.assertTrue(result["factors"][0]["eligible"])
        self.assertGreater(result["factors"][0]["holdout_ic"], 0)

    def test_exploratory_factor_is_labeled_and_formal_gate_stays_closed(self):
        moment = datetime(2026, 9, 23, 10, 31, tzinfo=factor_lab.CHINA)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            evaluation = {
                "eligibility_version": factor_lab.EVALUATION_VERSION,
                "last_day": "2026-09-22", "target": "至收盘涨幅",
                "universe_method": "test", "universe": [{"symbol": "600001"}],
                "days": 20, "samples": 100, "holdout_days": 4,
                "correlations": {"gap": {}},
                "factors": [{"key": "gap", "name": "开盘缺口", "train_ic": 0.0163,
                             "validation_ic": 0.0607, "validation_positive_share": 0.661,
                             "holdout_ic": 0.0261, "direction": 1,
                             "eligible": False, "exploratory": True}],
            }
            (base / "evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
            with patch.object(factor_lab, "EVALUATION", base / "evaluation.json"), \
                 patch.object(factor_lab, "MODEL", base / "model.json"), \
                 patch.object(factor_lab, "now_cn", return_value=moment):
                model = factor_lab.select_factors()
            self.assertEqual(model["quality"], "exploratory")
            self.assertEqual(model["factors"][0]["key"], "gap")
            self.assertIn("模拟研究", model["selection_warning"])
            self.assertFalse(evaluation["factors"][0]["eligible"])

    def test_live_ranking_is_published_from_current_snapshot(self):
        moment = datetime(2026, 9, 23, 10, 31, tzinfo=factor_lab.CHINA)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            model = {"model_id": "test-1", "selected_at": moment.isoformat(),
                     "universe_method": "test", "universe": [{"symbol": f"600{index:03d}"} for index in range(15)],
                     "factors": [{"key": "open_momentum", "name": "开盘以来动量", "weight": 1.0,
                                  "direction": 1, "holdout_ic": 0.2}]}
            (base / "model.json").write_text(json.dumps(model))
            rows = []
            for index in range(15):
                price = 100 + index
                rows.append({"代码": f"600{index:03d}", "名称": f"股票{index}", "最新价": price,
                             "今开": 100, "昨收": 99, "最高": price + 1, "最低": 99,
                             "成交量": 1000, "成交额": price * 100000})
            fake_ak = types.SimpleNamespace(tool_trade_date_hist_sina=lambda: pd.DataFrame({"trade_date": [moment.date()]}))
            with patch.object(factor_lab, "STORE", base), patch.object(factor_lab, "MODEL", base / "model.json"), \
                 patch.object(factor_lab, "LATEST", base / "latest.json"), \
                 patch.object(factor_lab, "now_cn", return_value=moment), \
                 patch.object(factor_lab, "spot_frame", return_value=pd.DataFrame(rows)), \
                 patch.object(factor_lab, "urlopen", side_effect=OSError("web offline")), \
                 patch.dict(sys.modules, {"akshare": fake_ak}):
                result = factor_lab.rank()
            self.assertEqual(result["ranking"][0]["symbol"], "600014")
            self.assertEqual(json.loads((base / "latest.json").read_text())["model_id"], "test-1")


if __name__ == "__main__":
    unittest.main()
