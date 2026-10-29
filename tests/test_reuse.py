import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab.cli import main, parser
from awx_lab.cluster import Cluster, deployments_ready
from awx_lab.lab import Lab
from awx_lab.system import LabError, atomic_json, lock


def make_lab(root):
    args = vars(parser().parse_args(["up", "--runtime", "podman", "--port", "8123"]))
    lab = Lab(root, {key: value for key, value in args.items() if key not in ("command", "state_dir")})
    lab.state.update(cluster_created=True, bootstrapped=True, status="ready", build={"image_id": "sha256:original"},
                     sources={"awx": {"merge_sha": "old", "head_sha": "old", "base_sha": "base"},
                              "awx-operator": {"sha": "operator"}})
    lab.save()
    atomic_json(root / "current.json", {"id": lab.state["id"]})
    return lab


class ReuseTests(unittest.TestCase):
    def scenario(self, *, changed=True, failure=None, pending=False, no_cache=False, previous_plugin=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        lab = make_lab(Path(temporary.name))
        if previous_plugin:
            lab.state["sources"]["awx-plugins"] = {"head_sha": "old-plugin"}
            lab.state["build"]["plugin_wheel"] = {"sha256": "old-plugin"}
        original = copy.deepcopy(lab.state)
        if pending:
            lab.state["pending_update"] = {"image": "previous-attempt"}
        sources = {"awx": copy.deepcopy(original["sources"]["awx"])}
        if changed:
            sources["awx"]["head_sha"] = sources["awx"]["merge_sha"] = "new"
        with contextlib.ExitStack() as stack:
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch("awx_lab.lab.doctor", return_value={"ok": True, "checks": []}))
            prepare = stack.enter_context(patch.object(lab, "prepare_sources", return_value=sources))
            existing = stack.enter_context(patch.object(lab.cluster, "require_existing"))
            builder = stack.enter_context(patch("awx_lab.lab.Builder"))
            builder.return_value.build.return_value = {"image_id": "sha256:new"}
            create = stack.enter_context(patch.object(lab.cluster, "create"))
            deploy = stack.enter_context(patch.object(lab.cluster, "deploy"))
            refresh = stack.enter_context(patch.object(lab.cluster, "refresh"))
            wait = stack.enter_context(patch.object(lab.cluster, "wait_ready"))
            delete = stack.enter_context(patch.object(lab.cluster, "delete"))
            stack.enter_context(patch.object(lab.cluster, "diagnostics"))
            checks = stack.enter_context(patch.object(lab, "run_checks"))
            if failure == "fetch":
                prepare.side_effect = LabError("merge conflict")
            elif failure == "build":
                builder.return_value.build.side_effect = LabError("build failed")
            elif failure == "rollout":
                refresh.side_effect = LabError("rollout failed")
            elif failure == "interrupt":
                refresh.side_effect = KeyboardInterrupt()
            elif failure == "test":
                checks.side_effect = LabError("test failed")
            if failure and failure != "interrupt":
                with self.assertRaises(LabError):
                    lab.refresh("https://github.com/ansible/awx/pull/8", suite="job", no_cache=no_cache)
            else:
                self.assertEqual(lab.refresh("https://github.com/ansible/awx/pull/8", suite="job", no_cache=no_cache), 130 if failure == "interrupt" else 0)
        create.assert_not_called()
        deploy.assert_not_called()
        delete.assert_not_called()
        existing.assert_called_once()
        self.assertEqual(lab.state["id"], original["id"])
        self.assertEqual(lab.state["url"], original["url"])
        self.assertTrue(lab.state["cluster_created"])
        self.assertEqual(len(list((lab.path / "history").glob("*/report.json"))), 1)
        return lab, original, builder, refresh, checks, output.getvalue()

    def test_refresh_updates_only_image_and_keeps_environment(self):
        lab, original, builder, refresh, checks, _ = self.scenario()
        builder.return_value.build.assert_called_once()
        refresh.assert_called_once_with(lab.state["image"])
        checks.assert_called_once_with("job")
        self.assertNotEqual(lab.state["image"], original["image"])
        self.assertEqual(lab.state["sources"]["awx"]["head_sha"], "new")
        self.assertEqual(lab.state["last_test_result"], "passed")
        self.assertNotIn("pending_update", lab.state)

    def test_unchanged_sources_skip_build_but_still_test(self):
        lab, original, builder, refresh, checks, output = self.scenario(changed=False)
        builder.assert_not_called()
        refresh.assert_not_called()
        checks.assert_called_once()
        self.assertEqual(lab.state["image"], original["image"])
        self.assertIn("skipping image build", output)

    def test_switching_to_awx_only_removes_previous_plugin_override(self):
        lab, _, builder, _, _, _ = self.scenario(previous_plugin=True)
        self.assertFalse(builder.return_value.build.call_args.kwargs["plugin"])
        self.assertNotIn("awx-plugins", lab.state["sources"])
        self.assertNotIn("plugin_wheel", lab.state["build"])
        self.assertIsNone(lab.state["selection"]["plugins_pr"])

    def test_failed_or_interrupted_rollout_never_tests_old_pods(self):
        for failure in ("rollout", "interrupt"):
            with self.subTest(failure=failure):
                lab, original, _, _, checks, _ = self.scenario(failure=failure)
                checks.assert_not_called()
                self.assertEqual(lab.state["sources"], original["sources"])
                self.assertIn("pending_update", lab.state)

    def test_fetch_or_build_failure_preserves_current_image(self):
        for failure in ("fetch", "build"):
            with self.subTest(failure=failure):
                lab, original, _, refresh, checks, _ = self.scenario(failure=failure)
                refresh.assert_not_called()
                checks.assert_not_called()
                self.assertEqual(lab.state["image"], original["image"])
                self.assertEqual(lab.state["sources"], original["sources"])

    def test_test_failure_retains_the_new_deployment_and_sources(self):
        lab, original, _, _, _, _ = self.scenario(failure="test")
        self.assertNotEqual(lab.state["image"], original["image"])
        self.assertEqual(lab.state["last_test_result"], "failed")

    def test_pending_update_and_no_cache_force_a_rollout_even_if_sources_match(self):
        for options in ({"pending": True}, {"no_cache": True}):
            with self.subTest(options=options):
                _, _, builder, refresh, _, _ = self.scenario(changed=False, **options)
                builder.return_value.build.assert_called_once()
                refresh.assert_called_once()

    def test_cli_reuse_uses_saved_settings_and_run_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lab = make_lab(root)
            command = ["--state-dir", str(root), "test", "https://github.com/ansible/awx/pull/8"]
            with patch("awx_lab.cli.Lab.existing") as attach:
                attach.return_value.refresh.return_value = 0
                self.assertEqual(main(command), 0)
                attach.return_value.refresh.assert_called_once_with(pr="https://github.com/ansible/awx/pull/8", plugins_pr=None, awx_ref=None, suite="smoke", no_cache=False)
                attach.reset_mock()
                with lock(lab.path), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(command), 1)
                attach.assert_not_called()

    def test_cli_requires_pr(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["test"])
        self.assertEqual(error.exception.code, 2)

    def test_cannot_reuse_removed_or_unbuilt_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            lab.state["cluster_created"] = False
            with self.assertRaisesRegex(LabError, "Create the environment"):
                lab.refresh("https://github.com/ansible/awx/pull/8")


class RolloutTests(unittest.TestCase):
    def deployments(self, image="new"):
        items = [{"metadata": {"name": name, "generation": 2}, "spec": {"replicas": 1,
                 "template": {"spec": {"containers": [{"name": name, "image": image}]}}},
                 "status": {"observedGeneration": 2, "updatedReplicas": 1, "replicas": 1,
                            "readyReplicas": 1, "availableReplicas": 1}} for name in ("awx-web", "awx-task")]
        items[0]["spec"]["template"]["spec"]["containers"][0]["readinessProbe"] = {
            "httpGet": {"path": "/api/v2/ping/", "port": 8052, "scheme": "HTTP"},
            "periodSeconds": 10, "timeoutSeconds": 5}
        return items

    def test_old_healthy_deployment_is_not_a_successful_update(self):
        self.assertFalse(deployments_ready(self.deployments("old"), "new"))
        self.assertTrue(deployments_ready(self.deployments(), "new"))
        for key, value in (("observedGeneration", 1), ("updatedReplicas", 0), ("replicas", 2), ("readyReplicas", 0)):
            data = self.deployments()
            data[1]["status"][key] = value
            self.assertFalse(deployments_ready(data, "new"))

    def test_running_web_without_http_readiness_never_passes(self):
        for change in ("missing", "path", "port", "scheme", "period", "timeout"):
            with self.subTest(change=change):
                data = self.deployments()
                container = data[0]["spec"]["template"]["spec"]["containers"][0]
                probe = container["readinessProbe"]
                if change == "missing":
                    del container["readinessProbe"]
                elif change in ("path", "port", "scheme"):
                    probe["httpGet"][change] = "wrong"
                else:
                    probe[change + "Seconds"] = 0
                self.assertFalse(deployments_ready(data, "new"))

    def test_refresh_loads_image_then_patches_application_image_and_web_settings(self):
        runner = Mock(env={})
        state = {"id": "awx-lab-test", "runtime": "podman"}
        cluster = Cluster(runner, Path("/tmp/awx-lab-test"), state)
        with patch.object(cluster, "load_image") as load, patch.object(cluster, "kubectl") as kubectl, patch.object(cluster, "wait_ready") as wait:
            cluster.refresh("localhost/awx-lab:new")
        load.assert_called_once_with("localhost/awx-lab:new")
        payload = json.loads(kubectl.call_args.args[-1])
        self.assertEqual(set(payload["spec"]), {"image", "image_version", "image_pull_policy",
                                               "ipv6_disabled", "web_readiness_period", "web_readiness_timeout"})
        self.assertEqual(payload["spec"]["image_pull_policy"], "IfNotPresent")
        self.assertTrue(payload["spec"]["ipv6_disabled"])
        self.assertEqual(payload["spec"]["web_readiness_period"], 10)
        wait.assert_called_once_with("localhost/awx-lab:new")

    def test_failed_image_import_never_patches_or_waits_for_old_deployments(self):
        cluster = Cluster(Mock(env={}), Path("/unused"), {"id": "awx-lab-test", "runtime": "podman"})
        with patch.object(cluster, "load_image", side_effect=LabError("import failed")), \
             patch.object(cluster, "kubectl") as kubectl, patch.object(cluster, "wait_ready") as wait, \
             self.assertRaisesRegex(LabError, "import failed"):
            cluster.refresh("localhost/awx-lab:new")
        kubectl.assert_not_called()
        wait.assert_not_called()


class CleanupTests(unittest.TestCase):
    def test_purge_removes_all_lab_images_but_preserves_shared_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            old = lab.state["image"]
            ui = "localhost/awx-lab-ui:" + lab.state["id"] + "-cache"
            new = old + "-next"
            lab.state.update(images=[old, ui, new], image=new)
            lab.save()
            present = "\n".join([old, ui, new, "localhost/awx-lab-tools:shared", "other:latest"])
            with patch("awx_lab.lab.Cluster") as cls, patch("awx_lab.lab.Runner") as runner, contextlib.redirect_stdout(io.StringIO()):
                runner.return_value.run.return_value = Mock(stdout=present, returncode=0)
                self.assertEqual(main(["--state-dir", directory, "down", "--purge"]), 0)
            removed = [call.args[0][-1] for call in runner.return_value.run.call_args_list if call.args[0][1:3] == ["image", "rm"]]
            self.assertEqual(set(removed), {old, ui, new})
            self.assertFalse(lab.path.exists())
            self.assertFalse((Path(directory) / "current.json").exists())

    def test_purge_failure_retains_state_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            def run(command, **kwargs):
                if command[1:3] == ["image", "rm"]:
                    raise LabError("image in use")
                return Mock(stdout=lab.state["image"], returncode=0)
            with patch("awx_lab.lab.Cluster") as cls, patch("awx_lab.lab.Runner") as runner, contextlib.redirect_stderr(io.StringIO()):
                runner.return_value.run.side_effect = run
                self.assertEqual(main(["--state-dir", directory, "down", "--purge"]), 1)
            self.assertTrue((lab.path / "state.json").exists())
            self.assertTrue((Path(directory) / "current.json").exists())
