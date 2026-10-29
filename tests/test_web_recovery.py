import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab.system import LabError
from test_reuse import make_lab


class WebRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.lab = make_lab(Path(temporary.name))
        self.lab.state["status"] = "failed"
        self.original = copy.deepcopy(self.lab.state)
        repository, tag = self.lab.state["image"].rsplit(":", 1)
        self.spec = {"image": repository, "image_version": tag, "ipv6_disabled": False,
                     "web_readiness_period": 0, "web_readiness_timeout": 1,
                     "postgres_storage_requirements": {"requests": {"storage": "8Gi"}},
                     "admin_password_secret": "existing-secret"}
        self.patches = []
        self.polls = 0

    def deployments(self, updated):
        result = []
        for name in ("awx-web", "awx-task"):
            container = {"name": name, "image": self.lab.state["image"]}
            if name == "awx-web" and updated:
                container["readinessProbe"] = {
                    "httpGet": {"path": "/api/v2/ping/", "port": 8052, "scheme": "HTTP"},
                    "periodSeconds": 10, "timeoutSeconds": 5}
            result.append({"metadata": {"name": name, "generation": 2 if updated else 1},
                           "spec": {"replicas": 1, "template": {"spec": {"containers": [container]}}},
                           "status": {"observedGeneration": 2 if updated else 1, "replicas": 1,
                                      "updatedReplicas": 1, "readyReplicas": 1, "availableReplicas": 1}})
        return result

    def kubectl(self, *args, **kwargs):
        if args == ("-n", "awx", "get", "awx", "awx", "-o", "json"):
            return Mock(stdout=json.dumps({"spec": self.spec}))
        if args[:7] == ("-n", "awx", "patch", "awx", "awx", "--type=merge", "-p"):
            changes = json.loads(args[-1])["spec"]
            self.patches.append(changes)
            self.spec.update(changes)
            return Mock(stdout="patched")
        if args == ("-n", "awx", "get", "deployments", "-o", "json"):
            self.polls += 1
            # Like Bill's log: old pods claim Ready without an HTTP probe.
            # The second poll simulates the Operator rolling out the fix.
            return Mock(stdout=json.dumps({"items": self.deployments(self.polls > 1)}))
        self.fail(f"Unexpected cluster operation: {args}")

    def test_up_repairs_failed_lab_without_rebuild_or_database_changes(self):
        self.recover("up")

    def test_unchanged_test_repairs_failed_lab_without_rebuild(self):
        self.recover("test")

    def recover(self, command):
        lab = self.lab
        original_spec = copy.deepcopy(self.spec)
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(lab, "prerequisites"))
            stack.enter_context(patch.object(lab, "retain_artifacts"))
            stack.enter_context(patch.object(lab.cluster, "start"))
            stack.enter_context(patch.object(lab.cluster, "require_existing"))
            stack.enter_context(patch.object(lab.cluster, "kubectl", side_effect=self.kubectl))
            sleep = stack.enter_context(patch("awx_lab.cluster.time.sleep"))
            prepare = stack.enter_context(patch.object(lab, "prepare"))
            stack.enter_context(patch.object(lab, "prepare_sources", return_value=copy.deepcopy(lab.state["sources"])))
            builder = stack.enter_context(patch("awx_lab.lab.Builder"))
            forbidden = [stack.enter_context(patch.object(lab.cluster, name))
                         for name in ("create", "delete", "deploy", "load_image")]
            def check(suite):
                self.assertEqual(suite, "smoke")
                self.assertEqual(self.polls, 2, "must wait for the HTTP probe rollout")
            checks = stack.enter_context(patch.object(lab, "run_checks", side_effect=check))
            result = lab.up() if command == "up" else lab.refresh("https://github.com/ansible/awx/pull/8")
            self.assertEqual(result, 0)
            checks.assert_called_once()
            builder.assert_not_called()
            prepare.assert_not_called()
            for method in forbidden:
                method.assert_not_called()
            sleep.assert_called_once_with(5)
        self.assertEqual(len(self.patches), 1)
        self.assertEqual(set(self.patches[0]), {"ipv6_disabled", "web_readiness_period", "web_readiness_timeout"})
        for key in ("image", "image_version", "postgres_storage_requirements", "admin_password_secret"):
            self.assertEqual(self.spec[key], original_spec[key])
        for key in ("id", "url", "image", "build", "sources"):
            self.assertEqual(lab.state[key], self.original[key])
        self.assertEqual(lab.state["status"], "ready")

    def test_configuration_repair_is_idempotent(self):
        with patch.object(self.lab.cluster, "kubectl", side_effect=self.kubectl):
            self.lab.cluster.configure_web()
            self.lab.cluster.configure_web()
        self.assertEqual(len(self.patches), 1)

    def test_failed_configuration_patch_never_checks_old_ready_pods(self):
        def reject_patch(*args, **kwargs):
            if "patch" in args:
                raise LabError("configuration patch rejected")
            return self.kubectl(*args, **kwargs)
        with patch.object(self.lab.cluster, "kubectl", side_effect=reject_patch), \
             self.assertRaisesRegex(LabError, "configuration patch rejected"):
            self.lab.cluster.wait_ready(self.lab.state["image"])
        self.assertEqual(self.polls, 0)

    def test_operator_never_rendering_probe_times_out(self):
        with patch.object(self.lab.cluster, "kubectl", side_effect=self.kubectl), \
             patch("awx_lab.cluster.time.monotonic", side_effect=[0, 0, 901]), \
             patch("awx_lab.cluster.time.sleep"), \
             self.assertRaisesRegex(LabError, "did not become ready"):
            self.lab.cluster.wait_ready(self.lab.state["image"])
        self.assertEqual(self.polls, 1)
        self.assertEqual(self.lab.state["image"], self.original["image"])
