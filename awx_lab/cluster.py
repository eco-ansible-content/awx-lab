"""One kind cluster, kubeconfig and loopback port per run."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import platform
import socket
import tempfile
import time

from .system import LabError, atomic_json


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def kind_config(port):
    return {"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4",
            "nodes": [{"role": "control-plane", "extraPortMappings": [
                {"containerPort": 30080, "hostPort": port, "listenAddress": "127.0.0.1", "protocol": "TCP"}]}]}


def awx_resource(image, ee_image):
    repository, tag = image.rsplit(":", 1)
    # The Operator also applies this policy to PostgreSQL, Redis and the EE.
    # AWX is imported before deployment; supporting images may still need pulling.
    return {"apiVersion": "awx.ansible.com/v1beta1", "kind": "AWX",
            "metadata": {"name": "awx", "namespace": "awx"},
            "spec": {"image": repository, "image_version": tag, "image_pull_policy": "IfNotPresent",
                     "service_type": "NodePort", "nodeport_port": 30080,
                     "projects_persistence": False, "postgres_storage_class": "standard",
                     "postgres_storage_requirements": {"requests": {"storage": "8Gi"}},
                     "control_plane_ee_image": ee_image,
                     "ee_images": [{"name": "AWX Lab EE", "image": ee_image}],
                     "web_resource_requirements": {"requests": {"cpu": "250m", "memory": "512Mi"}},
                     "task_resource_requirements": {"requests": {"cpu": "250m", "memory": "512Mi"}}}}


def deployments_ready(items, image):
    deployments = {item["metadata"]["name"]: item for item in items}
    for name in ("awx-web", "awx-task"):
        deployment = deployments.get(name, {})
        spec, status = deployment.get("spec", {}), deployment.get("status", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        if not any(container.get("name") == name and container.get("image") == image for container in containers):
            return False
        desired = spec.get("replicas", 1)
        if desired < 1 or status.get("observedGeneration", 0) < deployment["metadata"].get("generation", 1):
            return False
        if status.get("updatedReplicas", 0) != desired or status.get("replicas", 0) != desired:
            return False
        if status.get("availableReplicas", 0) < desired or status.get("readyReplicas", 0) < desired:
            return False
    return True


class Cluster:
    def __init__(self, runner, path: Path, state):
        self.runner, self.path, self.state = runner, path, state
        self.runner.env["KIND_EXPERIMENTAL_PROVIDER"] = state["runtime"]
        self.kubeconfig = path / "kubeconfig"

    def kubectl(self, *args, **kwargs):
        return self.runner.run(["kubectl", "--kubeconfig", self.kubeconfig,
                                "--context", f"kind-{self.state['id']}", "--request-timeout=30s", *args], **kwargs)

    def create(self):
        config = self.path / "kind.json"
        atomic_json(config, kind_config(self.state["port"]))
        self.runner.run(["kind", "create", "cluster", "--name", self.state["id"], "--config", config,
                         "--kubeconfig", self.kubeconfig, "--image", self.state["settings"]["node_image"], "--wait", "180s"], timeout=600)
        self.kubeconfig.chmod(0o600)
        self.load_image(self.state["image"])

    def load_image(self, image):
        if self.state["runtime"] == "podman":
            # kind's docker-image loader invokes Docker even with the Podman provider.
            # Keep the archive in the workspace and remove it on success or failure.
            with tempfile.TemporaryDirectory(prefix=".image-import-", dir=self.path) as directory:
                archive = Path(directory) / "awx.tar"
                self.runner.run(["podman", "save", "--format", "docker-archive", "--output", archive, image], timeout=600)
                self.runner.run(["kind", "load", "image-archive", archive, "--name", self.state["id"]], timeout=600)
        else:
            self.runner.run(["kind", "load", "docker-image", image, "--name", self.state["id"]], timeout=600)

    def operator_manifests(self):
        directory = self.path / "deployment"
        directory.mkdir(exist_ok=True)
        version = self.state["settings"]["operator_version"]
        # JSON is valid YAML, but Kustomize only discovers its supported filenames.
        manifest = {
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "namespace": "awx",
            "resources": ["../awx-operator/config/default"],
            "images": [{"name": "quay.io/ansible/awx-operator", "newTag": version},
                       # Kubebuilder retired its GCR registry. Keep the upstream tag.
                       {"name": "gcr.io/kubebuilder/kube-rbac-proxy", "newName": "quay.io/brancz/kube-rbac-proxy"}]}
        if self.state["runtime"] == "podman" and self.state.get("architecture") == "arm64" and platform.system() == "Darwin":
            # The Operator's Ansible process hits the same VM CPU issue as our helper.
            manifest["patches"] = [{"target": {"group": "apps", "version": "v1", "kind": "Deployment",
                                               "name": "awx-operator-controller-manager"}, "patch": json.dumps({
                "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "awx-operator-controller-manager"},
                "spec": {"template": {"spec": {"containers": [{"name": "awx-manager", "env": [
                    {"name": "OPENSSL_armcap", "value": "0"}]}]}}}})}]
        atomic_json(directory / "kustomization.yaml", manifest)
        return directory

    def deploy(self):
        directory = self.operator_manifests()
        self.kubectl("apply", "-k", directory, timeout=180)
        self.kubectl("wait", "--for=condition=Established", "crd/awxs.awx.ansible.com", "--timeout=120s", timeout=150)
        self.kubectl("-n", "awx", "rollout", "status", "deployment/awx-operator-controller-manager", "--timeout=300s", timeout=330)
        atomic_json(directory / "awx.json", awx_resource(self.state["image"], self.state["settings"]["ee_image"]))
        self.kubectl("apply", "-f", directory / "awx.json")
        self.wait_ready(self.state["image"])

    def require_existing(self):
        if not self.kubeconfig.is_file():
            raise LabError("This environment has no kubeconfig. Create it first with 'awx-lab up'.")
        self.kubectl("-n", "awx", "get", "awx", "awx", "-o", "name", capture=True)

    def prune_images(self, candidates):
        """Remove unused lab images from kind, protecting all aliases and containers."""
        from .retention import owned_image
        if any(not owned_image(self.state, tag) for tag in candidates):
            raise LabError("Refusing to remove an image not owned by this lab.")
        pods = json.loads(self.kubectl("get", "pods", "--all-namespaces", "-o", "json", capture=True).stdout)["items"]
        active = set()
        for pod in pods:
            if pod.get("status", {}).get("phase") not in ("Succeeded", "Failed"):
                for key in ("containers", "initContainers", "ephemeralContainers"):
                    active.update(c["image"] for c in pod["spec"].get(key, []))
        candidates = set(candidates) - active
        nodes = self.nodes()
        if not nodes:
            raise LabError("Cannot clean images: the lab's kind node is missing.")
        removed = set(candidates)
        for node in nodes:
            command = [self.state["runtime"], "exec", node, "crictl"]
            def inventory():
                return json.loads(self.runner.run([*command, "images", "--output", "json"], capture=True).stdout)["images"]
            containers = json.loads(self.runner.run([*command, "ps", "--all", "--output", "json"], capture=True).stdout)["containers"]
            used = {value for c in containers if c.get("state") != "CONTAINER_EXITED"
                    for value in (c.get("imageRef"), c.get("image", {}).get("image")) if value}
            for item in inventory():
                tags = set(item.get("repoTags", []))
                # CRI removes every alias of an image ID. Retained/untracked aliases,
                # digest references and existing containers make it ineligible.
                if (tags and tags <= candidates and not item.get("repoDigests") and
                        not item.get("pinned") and not tags & used and item["id"] not in used):
                    self.runner.run([*command, "rmi", item["id"]], capture=True, timeout=120)
            remaining = {tag for item in inventory() for tag in item.get("repoTags", [])}
            removed -= remaining
        return removed

    def refresh(self, image):
        self.load_image(image)
        repository, tag = image.rsplit(":", 1)
        patch = {"spec": {"image": repository, "image_version": tag, "image_pull_policy": "IfNotPresent"}}
        self.kubectl("-n", "awx", "patch", "awx", "awx", "--type=merge", "-p", json.dumps(patch))
        self.wait_ready(image)

    def wait_ready(self, image):
        deadline = time.monotonic() + self.state["settings"]["ready_timeout"]
        self.runner.write("Waiting for AWX web and task deployments to run the requested image (including migrations)…\n")
        while time.monotonic() < deadline:
            result = self.kubectl("-n", "awx", "get", "deployments", "-o", "json", capture=True)
            if deployments_ready(json.loads(result.stdout)["items"], image):
                return
            time.sleep(5)
        raise LabError("AWX did not become ready before the timeout. Inspect the saved pod and operator logs.")

    def credentials(self):
        result = self.kubectl("-n", "awx", "get", "secret", "awx-admin-password", "-o", "json", capture=True, sensitive=True)
        return "admin", base64.b64decode(json.loads(result.stdout)["data"]["password"]).decode()

    def diagnostics(self):
        directory = self.path / "diagnostics"
        directory.mkdir(exist_ok=True)
        commands = {
            "pods.txt": ("get", "pods", "-o", "wide"),
            "events.txt": ("get", "events", "--sort-by=.metadata.creationTimestamp"),
            "describe.txt": ("describe", "pods"),
            "operator.log": ("logs", "deployment/awx-operator-controller-manager", "--all-containers=true", "--tail=1000"),
            "web.log": ("logs", "deployment/awx-web", "--all-containers=true", "--tail=1000"),
            "task.log": ("logs", "deployment/awx-task", "--all-containers=true", "--tail=1000"),
        }
        for filename, command in commands.items():
            try:
                output = self.kubectl("-n", "awx", *command, capture=True, check=False, timeout=40).stdout
            except LabError as error:
                output = str(error)
            (directory / filename).write_text(output)

    def delete(self):
        self.runner.run(["kind", "delete", "cluster", "--name", self.state["id"], "--kubeconfig", self.kubeconfig], timeout=180)

    def nodes(self):
        result = self.runner.run([self.state["runtime"], "ps", "--all", "--filter",
                                  f"label=io.x-k8s.kind.cluster={self.state['id']}",
                                  "--format", "{{.ID}}"], capture=True)
        return result.stdout.split()

    def running(self, nodes):
        result = self.runner.run([self.state["runtime"], "inspect", *nodes], capture=True)
        states = json.loads(result.stdout)
        if len(states) != len(nodes):
            raise LabError("Could not inspect every environment node.")
        return [node["Id"] for node in states if node["State"]["Running"]]

    def stop(self):
        nodes = self.nodes()
        if not nodes:
            raise LabError("The environment's node container is missing. Use 'awx-lab down --purge' to clear its saved state.")
        running = self.running(nodes)
        if running:
            self.runner.run([self.state["runtime"], "stop", "--time", "60", *running], timeout=180)
        if self.running(nodes):
            raise LabError("The environment did not stop; its saved state has been retained.")

    def start(self):
        nodes = self.nodes()
        if not nodes:
            raise LabError("The environment's node container is missing. Use 'awx-lab down --purge', then 'awx-lab up'.")
        running = set(self.running(nodes))
        stopped = [node for node in nodes if not any(full.startswith(node) for full in running)]
        if stopped:
            self.runner.run([self.state["runtime"], "start", *stopped], timeout=180)
        # Export only into this environment's file; runtime port bindings can change.
        self.runner.run(["kind", "export", "kubeconfig", "--name", self.state["id"],
                         "--kubeconfig", self.kubeconfig], capture=True, timeout=60)
        self.kubeconfig.chmod(0o600)
        deadline = time.monotonic() + self.state["settings"]["ready_timeout"]
        while time.monotonic() < deadline:
            result = self.kubectl("get", "--raw=/readyz", capture=True, check=False)
            if result.returncode == 0:
                return
            time.sleep(3)
        raise LabError("Kubernetes did not recover after starting the retained node. The environment and data are preserved; inspect its runtime logs.")

    def verify_plugin(self, sha256):
        # pip's PEP 610 metadata records the wheel actually installed in the running image.
        script = ("import importlib.metadata,json; "
                  "d=json.loads(importlib.metadata.distribution('awx-plugins-core').read_text('direct_url.json')); "
                  "print(json.dumps(d)); "
                  f"assert d.get('archive_info',{{}}).get('hashes',{{}}).get('sha256') == {sha256!r}, 'plugin wheel hash mismatch'")
        self.kubectl("-n", "awx", "exec", "deployment/awx-task", "-c", "awx-task", "--",
                     "/var/lib/awx/venv/awx/bin/python", "-c", script, timeout=60)
