from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab.build import Builder, inject_plugin, ui_cache_key
from awx_lab.cluster import awx_resource, kind_config
from awx_lab.system import LabError, architecture


class BuildTests(unittest.TestCase):
    def test_wheel_overrides_plugin_after_cached_dependencies_and_preserves_extras(self):
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
            self.assertIn("awx-plugins-core[credentials-github-app] @ git+https://", requirements.read_text())
            self.assertIn("other @ git+https://example.test/other", requirements.read_text())
            self.assertEqual(len(info["sha256"]), 64)
            dockerfile = (awx / "Dockerfile").read_text()
            self.assertGreater(dockerfile.index("COPY requirements/awx_plugins"), dockerfile.index("RUN make requirements_awx"))
            self.assertIn("uninstall --yes awx-plugins-core", dockerfile)
            self.assertIn(wheel.name + "[credentials-github-app]", dockerfile)
            self.assertNotIn("--no-deps", dockerfile)
            self.assertEqual((awx / "requirements" / wheel.name).read_bytes(), wheel.read_bytes())

    def test_ui_cache_survives_backend_changes_but_invalidates_build_inputs(self):
        def key(rows, stage="FROM base AS ui-builder\nCOPY . /src\nRUN make ui\n", arch="arm64"):
            entries = "\0".join(f"100644 blob {sha}\t{path}" for path, sha in rows)
            with patch("awx_lab.build.git", return_value=entries):
                return ui_cache_key(Mock(), Path("/src"), stage, arch)
        base = [("awx/main/tasks.py", "a"), ("awx/ui/Makefile", "b"), ("requirements/requirements.txt", "c")]
        self.assertEqual(key(base), key([("awx/main/tasks.py", "changed"), *base[1:]]))
        self.assertNotEqual(key(base), key([base[0], ("awx/ui/Makefile", "changed"), base[2]]))
        self.assertNotEqual(key(base), key([*base[:2], ("requirements/requirements.txt", "changed")]))
        self.assertNotEqual(key(base), key(base, arch="amd64"))
        self.assertNotEqual(key(base), key(base, stage="FROM other AS ui-builder\nRUN make ui\n"))

    def test_cached_ui_is_used_without_rebuilding_and_no_cache_refreshes_it(self):
        for no_cache, available, expected_builds in ((False, True, 1), (False, False, 2), (True, True, 2)):
            with self.subTest(no_cache=no_cache, available=available), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                awx = root / "awx"
                awx.mkdir()
                original = "FROM ui-base AS ui-builder\nCOPY . /tmp/src/\nRUN make ui\nFROM backend AS builder\nCOPY --from=ui-builder /tmp/src/awx/ui/build /ui\n"
                runner = Mock()
                def run(command, **kwargs):
                    if command[1:3] == ["image", "inspect"] and "--format" not in command:
                        return Mock(returncode=0 if available else 1, stdout="{}")
                    return Mock(returncode=0, stdout="sha256:image")
                runner.run.side_effect = run
                builder = Builder(runner, "podman", root, "arm64")
                staged = []
                def in_tools(source, command, capture=False):
                    staged.append(source)
                    if command[0] == "make":
                        (source / "Dockerfile").write_text(original)
                    return Mock(stdout="1.0.0")
                with patch.object(builder, "tools"), patch.object(builder, "in_tools", side_effect=in_tools), \
                     patch("awx_lab.build.ui_cache_key", return_value="cachekey"):
                    result = builder.build("localhost/awx-lab:test", no_cache=no_cache)
                builds = [call.args[0] for call in runner.run.call_args_list if call.args[0][1] == "build"]
                self.assertEqual(len(builds), expected_builds)
                self.assertFalse((awx / "Dockerfile").exists())
                self.assertTrue(staged)
                self.assertTrue(all(source != awx for source in staged))
                self.assertTrue(all(not source.exists() for source in staged))
                app_build = builds[-1]
                self.assertIn(staged[0] / "Dockerfile", app_build)

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

    @patch("awx_lab.build.platform.system", return_value="Linux")
    def test_podman_mount_uses_selinux_label_and_keep_id_on_linux(self, _system):
        runner = Mock()
        builder = Builder(runner, "podman", Path("/tmp/run"), "amd64")
        builder.in_tools(Path("/tmp/run/awx"), ["make", "Dockerfile"])
        command = runner.run.call_args.args[0]
        self.assertIn(f"--userns=keep-id:uid={os.getuid()},gid={os.getgid()}", command)
        self.assertIn("/tmp/run/awx:/src:Z", command)
        self.assertNotIn("label=disable", command)
        self.assertNotIn("OPENSSL_armcap=0", command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))

    @patch("awx_lab.build.platform.system", return_value="Darwin")
    def test_podman_macos_shared_mount_does_not_relabel_files(self, _system):
        runner = Mock()
        builder = Builder(runner, "podman", Path("/tmp/run"), "arm64")
        for checkout in ("awx", "awx-plugins"):
            with self.subTest(checkout=checkout):
                source = Path("/tmp/run") / checkout
                builder.in_tools(source, ["python", "--version"])
                command = runner.run.call_args.args[0]
                self.assertEqual(command[command.index("--volume") + 1], f"{source}:/src")
                self.assertEqual(command[command.index("--security-opt") + 1], "label=disable")
                self.assertEqual(command[command.index("--env") + 1], "OPENSSL_armcap=0")
                self.assertIn(f"--userns=keep-id:uid={os.getuid()},gid={os.getgid()}", command)
                self.assertEqual(command[command.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
                self.assertIn("--rm", command)
                self.assertNotIn("--privileged", command)
                self.assertNotIn("/var/run/docker.sock", " ".join(command))

    def test_docker_mount_has_no_podman_security_options(self):
        for system in ("Darwin", "Linux"):
            with self.subTest(system=system), patch("awx_lab.build.platform.system", return_value=system):
                runner = Mock()
                builder = Builder(runner, "docker", Path("/tmp/run"), "arm64" if system == "Darwin" else "amd64")
                builder.in_tools(Path("/tmp/run/awx"), ["make", "Dockerfile"])
                command = runner.run.call_args.args[0]
                self.assertEqual(command[command.index("--volume") + 1], "/tmp/run/awx:/src")
                self.assertNotIn("--security-opt", command)
                self.assertNotIn("OPENSSL_armcap=0", command)
                self.assertFalse(any(arg.startswith("--userns") for arg in command))

    def test_loopback_network_and_exact_image(self):
        mapping = kind_config(8123)["nodes"][0]["extraPortMappings"][0]
        self.assertEqual(mapping["listenAddress"], "127.0.0.1")
        self.assertEqual(mapping["hostPort"], 8123)
        spec = awx_resource("localhost/awx-lab:run1", "registry/ee:tag")["spec"]
        self.assertEqual(spec["image_version"], "run1")
        self.assertEqual(spec["image_pull_policy"], "IfNotPresent")
        self.assertFalse(spec["projects_persistence"])
