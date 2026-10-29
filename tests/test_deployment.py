import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab.cluster import Cluster
from awx_lab.system import LabError, atomic_json


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="awx lab deployment ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "deployment"
        self.state = {"id": "awx-lab-test", "runtime": "podman", "image": "localhost/awx-lab:test",
                      "settings": {"operator_version": "2.19.1", "ee_image": "quay.io/ansible/awx-ee:24.6.1"}}
        self.cluster = Cluster(Mock(env={}), self.root, self.state)

    def generate(self):
        with patch.object(self.cluster, "kubectl") as kubectl, patch.object(self.cluster, "wait_ready"):
            self.cluster.deploy()
        return kubectl

    def test_deployment_generates_discoverable_kustomization_before_apply(self):
        def kubectl(*args, **kwargs):
            if args[:2] == ("apply", "-k"):
                self.assertEqual(args[2], self.directory)
                recognized = [self.directory / name for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")]
                self.assertEqual(sum(path.is_file() for path in recognized), 1)
                overlay = json.loads((self.directory / "kustomization.yaml").read_text())
                self.assertEqual(overlay["resources"], ["../awx-operator/config/default"])
        with patch.object(self.cluster, "kubectl", side_effect=kubectl), patch.object(self.cluster, "wait_ready"):
            self.cluster.deploy()

    def test_retry_with_legacy_json_updates_the_supported_file(self):
        self.directory.mkdir()
        atomic_json(self.directory / "kustomization.json", {"legacy": True})
        self.generate()
        self.state["settings"]["operator_version"] = "test-version"
        self.generate()
        document = json.loads((self.directory / "kustomization.yaml").read_text())
        self.assertEqual(document["images"][0]["newTag"], "test-version")
        self.assertEqual(document["resources"], ["../awx-operator/config/default"])

    def test_new_lab_disables_ipv6_listener_and_enables_http_readiness(self):
        self.generate()
        spec = json.loads((self.directory / "awx.json").read_text())["spec"]
        self.assertTrue(spec["ipv6_disabled"])
        self.assertEqual(spec["web_readiness_period"], 10)
        self.assertEqual(spec["web_readiness_timeout"], 5)
        self.assertEqual(spec["image"], "localhost/awx-lab")
        self.assertEqual(spec["image_version"], "test")
        self.assertEqual(spec["postgres_storage_requirements"]["requests"]["storage"], "8Gi")

    def test_operator_apply_failure_does_not_submit_awx_or_wait_for_readiness(self):
        with patch.object(self.cluster, "kubectl", side_effect=LabError("manifest invalid")) as kubectl, \
             patch.object(self.cluster, "wait_ready") as wait, self.assertRaisesRegex(LabError, "manifest invalid"):
            self.cluster.deploy()
        self.assertEqual(kubectl.call_count, 1)
        self.assertFalse((self.directory / "awx.json").exists())
        wait.assert_not_called()

    def test_operator_cpu_workaround_is_limited_to_macos_arm_podman(self):
        for system, arch, runtime in (("Darwin", "arm64", "podman"), ("Darwin", "arm64", "docker"),
                                       ("Linux", "arm64", "podman"), ("Linux", "amd64", "podman")):
            with self.subTest(system=system, arch=arch, runtime=runtime), patch("awx_lab.cluster.platform.system", return_value=system):
                self.state.update(architecture=arch, runtime=runtime)
                self.cluster.operator_manifests()
                overlay = json.loads((self.directory / "kustomization.yaml").read_text())
                self.assertEqual("patches" in overlay, (system, arch, runtime) == ("Darwin", "arm64", "podman"))

    @unittest.skipUnless(shutil.which("kubectl"), "kubectl is required for manifest rendering")
    def test_real_kubectl_renders_generated_overlay_with_legacy_json_present(self):
        base = self.root / "awx-operator" / "config" / "default"
        base.mkdir(parents=True)
        atomic_json(base / "kustomization.yaml", {"apiVersion": "kustomize.config.k8s.io/v1beta1",
                    "kind": "Kustomization", "resources": ["manager.json", "namespace.json"]})
        atomic_json(base / "namespace.json", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "system"}})
        atomic_json(base / "manager.json", {"apiVersion": "apps/v1", "kind": "Deployment",
                    "metadata": {"name": "awx-operator-controller-manager"}, "spec": {
                        "selector": {"matchLabels": {"app": "operator"}}, "template": {
                            "metadata": {"labels": {"app": "operator"}}, "spec": {"containers": [{
                                "name": "awx-manager", "image": "quay.io/ansible/awx-operator:latest"}, {
                                "name": "kube-rbac-proxy", "image": "gcr.io/kubebuilder/kube-rbac-proxy:v0.15.0"}]}}}})
        self.directory.mkdir()
        atomic_json(self.directory / "kustomization.json", {"legacy": True})
        atomic_json(self.directory / "namespace.json", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "awx"}})
        self.state["architecture"] = "arm64"
        with patch("awx_lab.cluster.platform.system", return_value="Darwin"):
            self.generate()
        result = subprocess.run([shutil.which("kubectl"), "kustomize", str(self.directory)],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("kind: Namespace"), 1)
        self.assertIn("namespace: awx", result.stdout)
        self.assertIn("name: awx-operator-controller-manager", result.stdout)
        self.assertIn("image: quay.io/ansible/awx-operator:2.19.1", result.stdout)
        self.assertIn("image: quay.io/brancz/kube-rbac-proxy:v0.15.0", result.stdout)
        self.assertNotIn("gcr.io/kubebuilder", result.stdout)
        self.assertIn("name: OPENSSL_armcap", result.stdout)
        self.assertIn('value: "0"', result.stdout)
        self.assertNotIn(":latest", result.stdout)
