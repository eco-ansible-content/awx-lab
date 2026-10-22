import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from awx_lab.system import CommandError, LabError, Runner, lock, run_path


class SystemTests(unittest.TestCase):
    def test_output_streaming_failure_and_sensitive_output(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "run.log"
            runner = Runner(log)
            with contextlib.redirect_stdout(io.StringIO()) as screen:
                runner.run([sys.executable, "-c", "print('streamed')"])
            self.assertIn("streamed", screen.getvalue())
            with self.assertRaises(CommandError) as error:
                runner.run([sys.executable, "-c", "import sys; print('bad'); sys.exit(9)"], capture=True)
            self.assertEqual(error.exception.returncode, 9)
            # Secret is produced by the child, not included in command arguments.
            with patch.dict(os.environ, {"LAB_TEST_SECRET": "never-log-this-secret"}):
                result = runner.run([sys.executable, "-c", "import os; print(os.environ['LAB_TEST_SECRET'])"], capture=True, sensitive=True)
            self.assertIn("never-log-this-secret", result.stdout)
            self.assertNotIn("never-log-this-secret", log.read_text())

    def test_github_token_not_inherited_by_build_subprocess(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret", "GH_TOKEN": "secret"}):
            result = Runner().run([sys.executable, "-c", "import os; print('GITHUB_TOKEN' in os.environ or 'GH_TOKEN' in os.environ)"], capture=True)
        self.assertEqual(result.stdout.strip(), "False")

    def test_timeout_stops_silent_child(self):
        start = time.monotonic()
        with self.assertRaisesRegex(LabError, "Timed out"):
            Runner().run([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.2, capture=True)
        self.assertLess(time.monotonic() - start, 3)

    def test_run_lock_rejects_concurrent_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with lock(path):
                with self.assertRaisesRegex(LabError, "busy"):
                    with lock(path):
                        self.fail("acquired twice")

    def test_path_traversal_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("../outside", "/tmp/awx-lab-foo", "awx-lab-foo/../outside"):
                with self.assertRaises(LabError):
                    run_path(root, name)
            (root / "awx-lab-link").symlink_to(root, target_is_directory=True)
            with self.assertRaises(LabError):
                run_path(root, "awx-lab-link")
