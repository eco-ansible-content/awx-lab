from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab.build import Builder, inject_plugin
from awx_lab.cluster import awx_resource, kind_config
from awx_lab.system import LabError, architecture


class BuildTests(unittest.TestCase):
    def test_wheel_replaces_only_plugin_requirement_preserves_extras_and_busts_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            awx = root / "awx"
            (awx / "requirements").mkdir(parents=True)
            requirements = awx / "requirements/requirements_git.txt"
            requirements.write_text("other @ git+https://example.test/other\nawx-plugins-core[credentials-github-app] @ git+https://github.com/ansible/awx-plugins.git@devel\n")
            (awx / "Dockerfile").write_text("FROM base\nADD requirements/requirements.txt /tmp/requirements/\nRUN make requirements_awx\n")
            wheel = root / "awx_plugins_core-0.1-py3-none-any.whl"
            wheel.write_bytes(b"wheel from merged PR")
            info = inject_plugin(awx, wheel)
            self.assertIn("awx-plugins-core[credentials-github-app] @ file:///tmp/awx-lab/", requirements.read_text())
            self.assertIn("other @ git+https://example.test/other", requirements.read_text())
            self.assertEqual(len(info["sha256"]), 64)
            dockerfile = (awx / "Dockerfile").read_text()
            self.assertLess(dockerfile.index("COPY requirements/awx_plugins"), dockerfile.index("RUN make requirements_awx"))
            self.assertEqual((awx / "requirements" / wheel.name).read_bytes(), wheel.read_bytes())

    def test_unknown_dependency_layout_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements").mkdir()
            (root / "requirements/requirements_git.txt").write_text("unexpected\n")
            with self.assertRaisesRegex(LabError, "Expected one"):
                inject_plugin(root, root / "plugin.whl")

    def test_architecture_mapping(self):
        for host in ("arm64", "aarch64"):
            self.assertEqual(architecture(host), "arm64")
        for host in ("x86_64", "amd64"):
            self.assertEqual(architecture(host), "amd64")
        with self.assertRaises(LabError):
            architecture("mips")

    def test_podman_mount_uses_selinux_label_and_keep_id_on_linux(self):
        runner = Mock()
        builder = Builder(runner, "podman", Path("/tmp/run"), "amd64")
        builder.in_tools(Path("/tmp/run/awx"), ["make", "Dockerfile"])
        command = runner.run.call_args.args[0]
        self.assertIn(f"--userns=keep-id:uid={os.getuid()},gid={os.getgid()}", command)
        self.assertIn("/tmp/run/awx:/src:Z", command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))

    def test_loopback_network_and_exact_image(self):
        mapping = kind_config(8123)["nodes"][0]["extraPortMappings"][0]
        self.assertEqual(mapping["listenAddress"], "127.0.0.1")
        self.assertEqual(mapping["hostPort"], 8123)
        spec = awx_resource("localhost/awx-lab:run1", "registry/ee:tag")["spec"]
        self.assertEqual(spec["image_version"], "run1")
        self.assertEqual(spec["image_pull_policy"], "Never")
        self.assertFalse(spec["projects_persistence"])
