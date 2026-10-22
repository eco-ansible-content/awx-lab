import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from awx_lab.system import CommandError, LabError, Runner, doctor, lock, run_path


class SystemTests(unittest.TestCase):
    def test_runtime_memory_blocks_small_vms_and_warns_below_recommended_size(self):
        for runtime in ("podman", "docker"):
            for gib, expected in ((1.9, "fail"), (4, "fail"), (7.4, "fail"),
                                  (7.5, "warn"), (8, "warn"), (11.6, "ok"), (15.6, "ok")):
                with self.subTest(runtime=runtime, gib=gib):
                    memory = int(gib * 1024**3)
                    data = ({"host": {"arch": "arm64", "memTotal": memory}} if runtime == "podman"
                            else {"Architecture": "aarch64", "MemTotal": memory})
                    with patch("awx_lab.system.Runner.run", return_value=Mock(stdout=json.dumps(data))), \
                         patch("awx_lab.system.shutil.which", return_value="/tools/tool"), \
                         patch("awx_lab.system.shutil.disk_usage", return_value=Mock(free=100 * 1024**3)), \
                         patch("awx_lab.system.platform.system", return_value="Darwin"), \
                         patch("awx_lab.system.architecture", return_value="arm64"):
                        result = doctor(runtime, Path("/tmp"))
                    check = next(item for item in result["checks"] if item["name"] == "runtime RAM")
                    self.assertEqual(check["status"], expected)
                    self.assertEqual(result["ok"], expected != "fail")
                    if expected == "fail":
                        self.assertIn("at least 8 GiB", check["detail"])
                        if runtime == "podman":
                            self.assertIn("prereq install", check["detail"])

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
