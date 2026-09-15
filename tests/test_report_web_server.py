import importlib.util
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("report_web_server", ROOT / "scripts" / "report_web_server.py")
report_web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_web)


class ReportWebServerTests(unittest.TestCase):
    def test_empty_report_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(report_web, "REPORT_DIR", Path(directory)):
            self.assertEqual(report_web.list_reports(), [])

    def test_lists_and_reads_reports_newest_first(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(report_web, "REPORT_DIR", Path(directory)):
            root = Path(directory)
            (root / "2026-09-11.md").write_text("# 旧日报", encoding="utf-8")
            (root / "2026-09-14.md").write_text("# 新日报", encoding="utf-8")
            (root / "notes.md").write_text("ignore", encoding="utf-8")
            self.assertEqual([item["date"] for item in report_web.list_reports()], ["2026-09-14", "2026-09-11"])
            report = report_web.read_report("2026-09-14.md")
            self.assertTrue(report["available"])
            self.assertEqual(report["markdown"], "# 新日报")

    def test_lists_morning_and_afternoon_reports_separately(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(report_web, "REPORT_DIR", Path(directory)):
            root = Path(directory)
            (root / "2026-09-15-a-morning.md").write_text("# 日报1", encoding="utf-8")
            (root / "2026-09-15-a-afternoon.md").write_text("# 日报2", encoding="utf-8")
            reports = report_web.list_reports()
            self.assertEqual([item["session"] for item in reports], ["afternoon", "morning"])
            self.assertEqual(reports[0]["id"], "2026-09-15-a-afternoon")
            self.assertIn("日报2", reports[0]["label"])

    def test_rejects_invalid_report_path(self):
        with self.assertRaises(ValueError):
            report_web.read_report("../portfolio.json")

    def test_publish_event_notifies_connected_clients(self):
        subscriber = queue.Queue()
        with report_web.SUBSCRIBERS_LOCK:
            report_web.SUBSCRIBERS.add(subscriber)
        try:
            report_web.publish_report_event("2026-09-14")
            self.assertIn("2026-09-14", subscriber.get_nowait())
        finally:
            with report_web.SUBSCRIBERS_LOCK:
                report_web.SUBSCRIBERS.discard(subscriber)


if __name__ == "__main__":
    unittest.main()
