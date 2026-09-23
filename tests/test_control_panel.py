import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("control_panel", ROOT / "scripts" / "control_panel.py")
control_panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control_panel)


class StreamCommandTests(unittest.TestCase):
    def test_chinese_error_keeps_its_text_in_log_and_exception(self):
        message = "错误：仅在 A 股交易时段 09:35–11:30、13:00–15:00 生成排名。"
        received = []
        with patch.dict(os.environ, {"PYTHONIOENCODING": "gbk"}):
            with self.assertRaises(RuntimeError) as caught:
                control_panel.stream_command(
                    [sys.executable, "-c", f"print({message!r}); raise SystemExit(1)"],
                    received.append,
                )
        self.assertEqual(received, [message])
        self.assertEqual(str(caught.exception), message)


if __name__ == "__main__":
    unittest.main()
