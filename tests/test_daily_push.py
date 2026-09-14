import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("daily_push", ROOT / "scripts" / "daily_push.py")
daily_push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily_push)


class DailyPushTests(unittest.TestCase):
    def test_default_schedule_is_0920(self):
        with tempfile.TemporaryDirectory() as directory:
            config = daily_push.load_config(Path(directory) / "missing.json")
        self.assertEqual(config["scheduled_time"], "09:20")

    def test_supported_webhook_payloads(self):
        self.assertEqual(daily_push.webhook_payload("feishu", "x")["msg_type"], "text")
        self.assertEqual(daily_push.webhook_payload("wecom", "x")["msgtype"], "markdown")
        self.assertEqual(daily_push.webhook_payload("dingtalk", "x")["markdown"]["text"], "x")

    def test_run_once_archives_and_pushes(self):
        config = {"enabled": True, "data_service_url": "http://test", "candidate_limit": 5, "webhook_type": "generic", "webhook_url": "http://hook"}
        report = {"is_trading_day": True, "markdown": "# report"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(daily_push, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(daily_push, "REPORT_DIR", Path(directory) / "reports"), \
             patch.object(daily_push, "get_json", return_value=report), \
             patch.object(daily_push, "notify_report_site") as notify, \
             patch.object(daily_push, "post_webhook") as post:
            self.assertTrue(daily_push.run_once(config, force=True))
            post.assert_called_once_with("http://hook", "generic", "# report")
            notify.assert_called_once()
            self.assertTrue(any((Path(directory) / "reports").glob("*.md")))

    def test_non_trading_day_does_not_push(self):
        config = {"enabled": True, "data_service_url": "http://test", "candidate_limit": 5, "webhook_url": "http://hook"}
        with patch.object(daily_push, "get_json", return_value={"is_trading_day": False}), \
             patch.object(daily_push, "post_webhook") as post:
            self.assertFalse(daily_push.run_once(config, force=True))
            post.assert_not_called()

    def test_no_holdings_still_archives_and_pushes_partial_report(self):
        config = {"enabled": True, "data_service_url": "http://test", "candidate_limit": 5, "webhook_url": "http://hook"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(daily_push, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(daily_push, "REPORT_DIR", Path(directory) / "reports"), \
             patch.object(daily_push, "get_json", return_value={"is_trading_day": True, "has_holdings": False, "markdown": "# 市场全景\n\n## 热门股票推荐排行"}), \
             patch.object(daily_push, "notify_report_site") as notify, \
             patch.object(daily_push, "post_webhook") as post:
            self.assertTrue(daily_push.run_once(config, force=True))
            post.assert_called_once()
            notify.assert_called_once()
            self.assertTrue(any((Path(directory) / "reports").glob("*.md")))

    def test_report_is_archived_when_realtime_site_notification_fails(self):
        config = {"enabled": True, "data_service_url": "http://test", "candidate_limit": 5, "webhook_url": ""}
        report = {"is_trading_day": True, "markdown": "# 市场全景"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(daily_push, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(daily_push, "REPORT_DIR", Path(directory) / "reports"), \
             patch.object(daily_push, "get_json", return_value=report), \
             patch.object(daily_push, "notify_report_site", side_effect=URLError("site offline")):
            self.assertTrue(daily_push.run_once(config, force=True))
            self.assertTrue(any((Path(directory) / "reports").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
