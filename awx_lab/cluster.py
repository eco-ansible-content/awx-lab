"""One kind cluster, kubeconfig and loopback port per run."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import socket
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
    return {"apiVersion": "awx.ansible.com/v1beta1", "kind": "AWX",
            "metadata": {"name": "awx", "namespace": "awx"},
            "spec": {"image": repository, "image_version": tag, "image_pull_policy": "Never",
                     "service_type": "NodePort", "nodeport_port": 30080,
                     "projects_persistence": False, "postgres_storage_class": "standard",
                     "postgres_storage_requirements": {"requests": {"storage": "8Gi"}},
                     "control_plane_ee_image": ee_image,
                     "ee_images": [{"name": "AWX Lab EE", "image": ee_image}],
                     "web_resource_requirements": {"requests": {"cpu": "250m", "memory": "512Mi"}},
                     "task_resource_requirements": {"requests": {"cpu": "250m", "memory": "512Mi"}}}}


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
        self.runner.run(["kind", "load", "docker-image", self.state["image"], "--name", self.state["id"]], timeout=600)

    def deploy(self):
        directory = self.path / "deployment"
        directory.mkdir()
        version = self.state["settings"]["operator_version"]
        atomic_json(directory / "namespace.json", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "awx"}})
        atomic_json(directory / "kustomization.json", {
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "namespace": "awx",
            "resources": ["../awx-operator/config/default", "namespace.json"],
            "images": [{"name": "quay.io/ansible/awx-operator", "newTag": version}]})
        self.kubectl("apply", "-k", directory, timeout=180)
        self.kubectl("wait", "--for=condition=Established", "crd/awxs.awx.ansible.com", "--timeout=120s", timeout=150)
        self.kubectl("-n", "awx", "rollout", "status", "deployment/awx-operator-controller-manager", "--timeout=300s", timeout=330)
        atomic_json(directory / "awx.json", awx_resource(self.state["image"], self.state["settings"]["ee_image"]))
        self.kubectl("apply", "-f", directory / "awx.json")
        deadline = time.monotonic() + self.state["settings"]["ready_timeout"]
        self.runner.write("Waiting for AWX web and task deployments (including database migrations)…\n")
        while time.monotonic() < deadline:
            result = self.kubectl("-n", "awx", "get", "deployments", "-o", "json", capture=True)
            deployments = {item["metadata"]["name"]: item for item in json.loads(result.stdout)["items"]}
            if all(deployments.get(name, {}).get("status", {}).get("availableReplicas", 0) >= 1
                   for name in ("awx-web", "awx-task")):
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

    def verify_plugin(self, sha256):
        # pip's PEP 610 metadata records the wheel actually installed in the running image.
        script = ("import importlib.metadata,json; "
                  "d=json.loads(importlib.metadata.distribution('awx-plugins-core').read_text('direct_url.json')); "
                  "print(json.dumps(d)); "
                  f"assert d.get('archive_info',{{}}).get('hashes',{{}}).get('sha256') == {sha256!r}, 'plugin wheel hash mismatch'")
        self.kubectl("-n", "awx", "exec", "deployment/awx-task", "-c", "awx-task", "--",
                     "/var/lib/awx/venv/awx/bin/python", "-c", script, timeout=60)
