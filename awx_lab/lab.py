"""Lifecycle and reports shared by interactive and automated runs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
import xml.etree.ElementTree as ET

from .build import Builder
from .checks import API
from .cluster import Cluster, free_port
from .source import PullRequest, checkout_ref, merge_pr
from .system import LabError, Runner, architecture, atomic_json, doctor, lock


def now():
    return datetime.now(timezone.utc).isoformat()


class Lab:
    def __init__(self, root: Path, options):
        run_id = "awx-lab-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.path = root / run_id
        self.path.mkdir(parents=True, mode=0o700)
        self.path.chmod(0o700)
        port = options.pop("port") or free_port()
        self.state = {"schema_version": 1, "id": run_id, "created_at": now(), "status": "starting",
                      "runtime": options.pop("runtime"), "architecture": architecture(), "port": port,
                      "url": f"http://127.0.0.1:{port}", "image": f"localhost/awx-lab:{run_id}",
                      "cluster_created": False, "settings": options, "sources": {}, "steps": []}
        self.runner = Runner(self.path / "run.log")
        self.cluster = Cluster(self.runner, self.path, self.state)
        self.save()

    def save(self):
        self.state["updated_at"] = now()
        atomic_json(self.path / "state.json", self.state)
        atomic_json(self.path / "report.json", self.state)
        steps = self.state["steps"]
        suite = ET.Element("testsuite", name="awx-lab", tests=str(len(steps)),
                           failures=str(sum(s["status"] in ("failed", "interrupted") for s in steps)),
                           time=str(sum(s.get("seconds", 0) for s in steps)))
        properties = ET.SubElement(suite, "properties")
        ET.SubElement(properties, "property", name="run_id", value=self.state["id"])
        for step in steps:
            case = ET.SubElement(suite, "testcase", classname="awx_lab", name=step["name"], time=str(step.get("seconds", 0)))
            if step["status"] in ("failed", "interrupted"):
                ET.SubElement(case, "failure", message=step.get("error", step["status"])).text = step.get("error", "")
        ET.ElementTree(suite).write(self.path / "junit.xml", encoding="utf-8", xml_declaration=True)

    @contextmanager
    def step(self, name):
        self.runner.write(f"\n== {name} ==\n")
        entry = {"name": name, "status": "running", "started_at": now()}
        self.state["steps"].append(entry)
        self.state["phase"] = name
        self.save()
        start = time.monotonic()
        try:
            yield
        except BaseException as error:
            entry.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
            raise
        else:
            entry["status"] = "passed"
        finally:
            entry["seconds"] = round(time.monotonic() - start, 3)
            self.save()

    def prepare(self):
        settings = self.state["settings"]
        primary = PullRequest.parse(settings["pr"])
        plugin = PullRequest.parse(settings["plugins_pr"]) if settings.get("plugins_pr") else None
        if primary.repo == "awx-plugins":
            plugin = primary
            self.state["sources"]["awx"] = checkout_ref(self.runner, "https://github.com/ansible/awx.git", settings["awx_ref"], self.path / "awx")
        else:
            self.state["sources"]["awx"] = merge_pr(self.runner, primary, self.path / "awx")
        if plugin:
            self.state["sources"]["awx-plugins"] = merge_pr(self.runner, plugin, self.path / "awx-plugins")
        self.state["sources"]["awx-operator"] = checkout_ref(
            self.runner, "https://github.com/ansible/awx-operator.git", settings["operator_version"], self.path / "awx-operator")

    def execute(self, command):
        settings = self.state["settings"]
        error = None
        with lock(self.path):
            try:
                with self.step("prerequisites"):
                    self.state["doctor"] = doctor(self.state["runtime"], self.path)
                    if not self.state["doctor"]["ok"]:
                        failures = [c["detail"] for c in self.state["doctor"]["checks"] if c["status"] == "fail"]
                        raise LabError("Prerequisites are missing:\n" + "\n".join(failures))
                with self.step("fetch and merge"):
                    self.prepare()
                with self.step("build AWX image"):
                    builder = Builder(self.runner, self.state["runtime"], self.path, self.state["architecture"], settings["build_timeout"])
                    self.state["build"] = builder.build(self.state["image"], plugin="awx-plugins" in self.state["sources"],
                                                        no_cache=settings["no_cache"], headless=settings["headless"])
                with self.step("create kind cluster"):
                    # Mark before creation so interrupted/partial clusters can be removed.
                    self.state["cluster_created"] = True
                    self.save()
                    self.cluster.create()
                with self.step("deploy AWX"):
                    self.cluster.deploy()
                with self.step("API smoke checks"):
                    username, password = self.cluster.credentials()
                    api = API(self.state["url"], username, password)
                    self.state["smoke"] = api.smoke()
                if "plugin_wheel" in self.state["build"]:
                    with self.step("verify installed plugin wheel"):
                        self.cluster.verify_plugin(self.state["build"]["plugin_wheel"]["sha256"])
                if command == "test" and settings["suite"] == "job":
                    with self.step("Ansible localhost ping job"):
                        self.state["job"] = api.job(self.state["id"])
                self.state["status"] = "ready" if command == "up" else "passed"
            except (Exception, KeyboardInterrupt) as caught:
                error = caught
                self.state["status"] = "interrupted" if isinstance(caught, KeyboardInterrupt) else "failed"
                self.state["error"] = str(caught) or "Interrupted by user"
            finally:
                if self.state["cluster_created"]:
                    try:
                        self.runner.write("Collecting pod and operator diagnostics…\n")
                        self.cluster.diagnostics()
                    except (LabError, OSError, KeyboardInterrupt) as diagnostic_error:
                        self.state["diagnostic_error"] = str(diagnostic_error)
                if command == "test" and not settings["keep"] and self.state["cluster_created"]:
                    try:
                        with self.step("cleanup"):
                            self.cluster.delete()
                            self.state["cluster_created"] = False
                    except (LabError, KeyboardInterrupt) as cleanup_error:
                        self.state["cleanup_error"] = str(cleanup_error)
                        self.state["status"] = "cleanup_failed"
                        if error is None:
                            error = cleanup_error
                self.save()
                self.runner.write(f"\nRun: {self.state['id']}\nResult: {self.state['status']}\nReports: {self.path}\n")
                if self.state["cluster_created"]:
                    self.runner.write(f"AWX: {self.state['url']}\nCredentials: awx-lab credentials {self.state['id']}\nCleanup: awx-lab down {self.state['id']}\n")
        if error:
            if isinstance(error, KeyboardInterrupt):
                return 130
            raise LabError(str(error)) from error
        return 0
