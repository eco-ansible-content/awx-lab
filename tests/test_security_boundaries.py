"""Regression checks for boundaries between untrusted source and the host."""

from pathlib import Path
import os
import subprocess
import tempfile
import time
import unittest

from awx_lab.build import Builder, inject_plugin
from awx_lab.source import clone, prepare_checkout, checkout_marker
from awx_lab.system import LabError, Runner
from awx_lab.terminal import CommandProcess


class BoundaryTests(unittest.TestCase):
    def test_legacy_checkout_is_recloned_without_reading_build_modified_git_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin"
            origin.mkdir()
            subprocess.run(["git", "init", str(origin)], check=True, capture_output=True)
            (origin / "file").write_text("source\n")
            subprocess.run(["git", "-C", str(origin), "add", "file"], check=True)
            subprocess.run(["git", "-C", str(origin), "-c", "user.name=Audit", "-c", "user.email=audit@localhost",
                            "commit", "-m", "base"], check=True, capture_output=True)
            checkout = root / "awx"
            clone(Runner(), str(origin), checkout)
            marker = checkout_marker(checkout)
            marker.unlink()  # A checkout from the older, writable-mount design.
            command_marker = root / "unexpected-host-command"
            script = checkout / "probe.sh"
            script.write_text(f'#!/bin/sh\nprintf probe > "{command_marker}"\nprintf "\\0"\n')
            script.chmod(0o700)
            subprocess.run(["git", "-C", str(checkout), "config", "core.fsmonitor", str(script)], check=True)
            prepare_checkout(Runner(), str(origin), checkout, reuse=True)
            self.assertFalse(command_marker.exists())
            self.assertTrue(marker.is_file())
            self.assertFalse(script.exists())

    def test_plugin_wheel_never_follows_host_file_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            awx = root / "awx"
            (awx / "requirements").mkdir(parents=True)
            (awx / "requirements/requirements_git.txt").write_text(
                "awx-plugins-core @ git+https://github.com/ansible/awx-plugins.git\n")
            (awx / "Dockerfile").write_text("FROM base\nRUN make requirements_awx\n")
            outside = root / "outside"
            outside.write_bytes(b"outside file")
            wheel = root / "awx_plugins_core-0.1-py3-none-any.whl"
            wheel.symlink_to(outside)
            with self.assertRaisesRegex(LabError, "regular wheel"):
                inject_plugin(awx, wheel)
            self.assertEqual(outside.read_bytes(), b"outside file")
            wheel.unlink()
            wheel.write_bytes(b"wheel contents")
            (awx / "requirements" / wheel.name).symlink_to(outside)
            with self.assertRaisesRegex(LabError, "existing wheel destination"):
                inject_plugin(awx, wheel)
            self.assertEqual(outside.read_bytes(), b"outside file")

    def test_tui_child_does_not_import_checkout_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "awx_lab"
            package.mkdir()
            (package / "__init__.py").write_text("")
            marker = root / "unexpected-import"
            (package / "__main__.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
            process = CommandProcess(["--help"], root, root / "state")
            output = ""
            try:
                deadline = time.monotonic() + 10
                while not process.finished and time.monotonic() < deadline:
                    output += process.poll()
                    time.sleep(0.02)
                self.assertTrue(process.finished)
                self.assertEqual(process.returncode, 0)
            finally:
                process.close()
            self.assertIn("usage:", output)
            self.assertFalse(marker.exists())

    def test_helper_mounts_only_disposable_checkout_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            awx = root / "awx"
            (awx / ".git").mkdir(parents=True)
            config = awx / ".git/config"
            config.write_text("[core]\n\tbare = false\n")
            commands = []
            class FakeRunner:
                def run(self, command, **kwargs):
                    commands.append(command)
                    if command[1:3] == ["image", "inspect"]:
                        return subprocess.CompletedProcess(command, 0, "sha256:image")
                    return subprocess.CompletedProcess(command, 0, "sha256:image")
                def write(self, message):
                    pass
            builder = Builder(FakeRunner(), "docker", root, "amd64")
            sources = []
            def fake_helper(source, command, capture=False):
                sources.append(source)
                if command[0] == "make":
                    (source / ".git/config").write_text("[core]\n\tfsmonitor = malicious\n")
                    (source / "Dockerfile").write_text("FROM base\n")
                return subprocess.CompletedProcess(command, 0, "1.0.0\n")
            from unittest.mock import patch
            with patch.object(builder, "tools"), patch.object(builder, "in_tools", side_effect=fake_helper):
                builder.build("localhost/awx-lab:test", headless=True)
            self.assertEqual(config.read_text(), "[core]\n\tbare = false\n")
            self.assertTrue(sources and all(source != awx for source in sources))
            self.assertTrue(all(not source.exists() for source in sources))


if __name__ == "__main__":
    unittest.main()
