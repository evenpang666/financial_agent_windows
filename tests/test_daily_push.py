import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("daily_push", ROOT / "scripts" / "daily_push.py")
daily_push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily_push)


class DailyPushTests(unittest.TestCase):
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

    def test_no_holdings_does_not_archive_or_push(self):
        config = {"enabled": True, "data_service_url": "http://test", "candidate_limit": 5, "webhook_url": "http://hook"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(daily_push, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(daily_push, "REPORT_DIR", Path(directory) / "reports"), \
             patch.object(daily_push, "get_json", return_value={"is_trading_day": None, "empty_reason": "no_holdings", "markdown": ""}), \
             patch.object(daily_push, "notify_report_site") as notify, \
             patch.object(daily_push, "post_webhook") as post:
            self.assertFalse(daily_push.run_once(config, force=True))
            post.assert_not_called()
            notify.assert_not_called()
            self.assertFalse((Path(directory) / "reports").exists())


if __name__ == "__main__":
    unittest.main()
