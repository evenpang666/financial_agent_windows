import importlib.util
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("linux_service", ROOT / "scripts" / "linux_service.py")
linux_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linux_service)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux process ownership check")
class LinuxServiceTests(unittest.TestCase):
    def test_managed_process_can_be_started_and_stopped(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(linux_service, "STATE", Path(directory)), \
             patch.object(linux_service, "PYTHON", Path(sys.executable)), \
             patch.object(linux_service, "SERVICES", {"daily": ([sys.executable, "-c", "import time; time.sleep(60)"], None)}):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                try:
                    linux_service.start("daily")
                    self.assertIsNotNone(linux_service.owned_process("daily"))
                finally:
                    linux_service.stop("daily")
            self.assertIsNone(linux_service.owned_process("daily"))


if __name__ == "__main__":
    unittest.main()
