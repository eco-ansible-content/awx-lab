"""Lifecycle and reports shared by interactive and automated runs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import shutil
import time
import uuid
import xml.etree.ElementTree as ET

from .build import Builder
from .checks import API
from .cluster import Cluster, free_port
from .local_source import LocalSource, snapshot_local
from .source import PullRequest, checkout_ref, merge_pr
from .retention import prune_history, prune_images, remember_success
from .system import LabError, Runner, architecture, atomic_json, doctor


def now():
    return datetime.now(timezone.utc).isoformat()


class Lab:
    def __init__(self, root: Path, options, *, log_mode="clean"):
        # Presentation belongs to this invocation, not the retained environment.
        log_mode = options.pop("log_mode", log_mode)
        run_id = "awx-lab-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.path = root / run_id
        self.path.mkdir(parents=True, mode=0o700)
        self.path.chmod(0o700)
        port = options.pop("port") or free_port()
        self.state = {"schema_version": 2, "id": run_id, "created_at": now(), "status": "starting",
                      "runtime": options.pop("runtime"), "architecture": architecture(), "port": port,
                      "url": f"http://127.0.0.1:{port}", "image": f"localhost/awx-lab:{run_id}",
                      "cluster_created": False, "settings": options, "sources": {}, "steps": []}
        self.runner = Runner(self.path / "run.log", log_mode=log_mode)
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
    def step(self, name, *, visible=False):
        self.runner.write(f"\n== {name} ==\n", level="progress" if visible else "detail")
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

    def track_image(self, image):
        images = self.state.setdefault("images", [self.state["image"]])
        if image not in images:
            images.append(image)
            self.save()

    def prepare_sources(self, selection=None):
        settings = selection or self.state["settings"]
        primary = PullRequest.parse(settings["pr"]) if settings.get("pr") else None
        plugin = PullRequest.parse(settings["plugins_pr"]) if settings.get("plugins_pr") else None
        local = settings.get("local")
        local = LocalSource(Path(local["path"]), local["repo"], local["branch"], local["head_sha"]) if local else None
        sources = {}
        awx = self.path / "awx"
        if local and local.repo == "awx":
            sources["awx"] = snapshot_local(self.runner, local, awx)
        elif primary is None or primary.repo == "awx-plugins":
            if primary:
                plugin = primary
            sources["awx"] = checkout_ref(self.runner, "https://github.com/ansible/awx.git", settings["awx_ref"],
                                           awx, reuse=awx.exists(), switch_remote=True)
        else:
            sources["awx"] = merge_pr(self.runner, primary, awx, reuse=awx.exists(), switch_remote=True)
        if local and local.repo == "awx-plugins":
            sources["awx-plugins"] = snapshot_local(self.runner, local, self.path / "awx-plugins")
        elif plugin:
            path = self.path / "awx-plugins"
            sources["awx-plugins"] = merge_pr(self.runner, plugin, path, reuse=path.exists(), switch_remote=True)
        return sources

    def prepare(self):
        self.state["sources"] = self.prepare_sources()
        path = self.path / "awx-operator"
        self.state["sources"]["awx-operator"] = checkout_ref(
            self.runner, "https://github.com/ansible/awx-operator.git", self.state["settings"]["operator_version"],
            path, reuse=path.exists())

    def prerequisites(self):
        self.state["doctor"] = doctor(self.state["runtime"], self.path)
        for check in self.state["doctor"]["checks"]:
            if check["status"] == "warn":
                self.runner.write(f"WARN {check['name']}: {check['detail']}\n", level="warning")
        if not self.state["doctor"]["ok"]:
            failures = [c["detail"] for c in self.state["doctor"]["checks"] if c["status"] == "fail"]
            raise LabError("Prerequisite checks failed:\n" + "\n".join(failures))

    def run_checks(self, suite):
        with self.step("API smoke checks", visible=True):
            username, password = self.cluster.credentials()
            api = API(self.state["url"], username, password)
            self.state["smoke"] = api.smoke()
            for check in self.state["smoke"]["checks"]:
                self.runner.write(f"PASS {check}\n", level="result")
        if "plugin_wheel" in self.state["build"]:
            with self.step("verify installed plugin wheel", visible=True):
                self.cluster.verify_plugin(self.state["build"]["plugin_wheel"]["sha256"])
                self.runner.write("PASS installed plugin wheel hash\n", level="result")
        if suite == "job":
            with self.step("Ansible localhost ping job", visible=True):
                self.state["job"] = api.job(self.state["id"])
                self.runner.write("PASS Ansible localhost ping job\n", level="result")

    @classmethod
    def existing(cls, path, state, *, log_mode="clean"):
        """Attach to a saved lab. Caller must hold its lock while refreshing."""
        lab = cls.__new__(cls)
        lab.path, lab.state = path, state
        if state.get("schema_version", 1) < 2:
            state["bootstrapped"] = bool(state.get("build") and state.get("cluster_created") and (
                state.get("status") in ("ready", "passed") or state.get("last_test_result") or
                any(step["name"] == "deploy AWX" and step["status"] == "passed" for step in state.get("steps", []))))
            state["schema_version"] = 2
        lab.runner = Runner(path / "run.log", log_mode=log_mode)
        lab.cluster = Cluster(lab.runner, path, state)
        return lab

    def operation(self, name, action):
        """Persist each attempt and retain the environment on every failure."""
        # Adopt a verified environment created before retention was introduced.
        if not self.state.get("last_good") and self.state.get("status") == "ready":
            remember_success(self.state)
        attempt = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        archive = self.path / "history" / attempt
        archive.mkdir(parents=True)
        for filename in ("state.json", "report.json", "junit.xml"):
            if (self.path / filename).exists():
                shutil.copy2(self.path / filename, archive / filename)
        if (self.path / "diagnostics").exists():
            shutil.move(self.path / "diagnostics", archive / "diagnostics")
        self.state.update(status="updating" if name == "test" else "starting", steps=[], attempt_id=attempt)
        for key in ("error", "smoke", "job", "diagnostic_error", "cleanup_error", "retention"):
            self.state.pop(key, None)
        if name == "test":
            self.state["last_test_result"] = "running"
        self.save()
        error = None
        try:
            action()
            self.state["status"] = "ready"
            if name == "test":
                self.state["last_test_result"] = "passed"
            remember_success(self.state)
        except (Exception, KeyboardInterrupt) as caught:
            error = caught
            self.state["status"] = "interrupted" if isinstance(caught, KeyboardInterrupt) else "failed"
            if name == "test":
                self.state["last_test_result"] = self.state["status"]
            self.state["error"] = str(caught) or "Interrupted by user"
            self.runner.write(f"Error during {self.state.get('phase', name)}: {self.state['error']}\n",
                              echo=False, level="error")
            if self.state["cluster_created"] and not self.state.get("stopped"):
                try:
                    self.cluster.diagnostics()
                except (LabError, OSError, KeyboardInterrupt) as diagnostic_error:
                    self.state["diagnostic_error"] = str(diagnostic_error)
        finally:
            self.retain_artifacts(clean_images=error is None)
            self.save()
            self.runner.write(f"\nResult: {self.state['status']}\nReports: {self.path}\n", level="result")
            if self.state["cluster_created"]:
                selected = self.state.get("requested_selection" if name == "test" else "selection", {})
                next_test = "awx-lab test --local (from your source checkout)" if selected.get("local") else "awx-lab test <PR_URL>"
                self.runner.write(f"AWX: {self.state['url']}\nCredentials: awx-lab credentials\n"
                                  f"Next test: {next_test}\nStop: awx-lab down\nDestroy: awx-lab down --purge\n",
                                  level="result")
        if isinstance(error, KeyboardInterrupt):
            self.runner.write(f"Interrupted during {self.state.get('phase', name)}.\nReports: {self.path}\n", level="error")
            return 130
        if error:
            raise LabError(f"{self.state.get('phase', name)}: {error}\nReports: {self.path}") from error
        return 0

    def retain_artifacts(self, clean_images):
        result = {"warnings": []}
        actions = {"removed_history": lambda: prune_history(self.path)}
        if clean_images:
            actions["images"] = lambda: prune_images(self)
        for name, action in actions.items():
            try:
                result[name] = action()
            except (LabError, OSError, ValueError, KeyError, KeyboardInterrupt) as error:
                message = str(error) or "Cleanup interrupted; remaining artifacts retained."
                result["warnings"].append(message)
                self.runner.write(f"WARN retention: {message}\n", level="warning")
        self.state["retention"] = result
        removed = result.get("images", {}).get("removed_images", [])
        if removed:
            self.runner.write(f"Storage cleanup: removed {len(removed)} obsolete lab image tag(s).\n")

    def apply_update(self, update):
        self.cluster.refresh(update["image"])
        self.state.update(image=update["image"], sources=update["sources"], build=update["build"],
                          selection=update.get("selection", self.state["settings"]))
        self.state.pop("pending_update", None)
        self.save()

    def up(self):
        self.runner.write("Starting AWX lab. Detailed output: awx-lab logs --follow\n", level="progress")
        def start():
            with self.step("prerequisites"):
                self.prerequisites()
                if self.state["architecture"] != architecture():
                    raise LabError("This environment uses a different architecture. Destroy it with 'awx-lab down --purge' before creating another.")
            if not self.state["cluster_created"]:
                if not self.state.get("build"):
                    with self.step("fetch baseline sources"):
                        self.prepare()
                    with self.step("build baseline AWX image"):
                        settings = self.state["settings"]
                        builder = Builder(self.runner, self.state["runtime"], self.path, self.state["architecture"],
                                          settings["build_timeout"], record_image=self.track_image)
                        self.state["build"] = builder.build(self.state["image"], plugin="awx-plugins" in self.state["sources"],
                                                            no_cache=settings["no_cache"], headless=settings["headless"])
                with self.step("create kind cluster"):
                    # Mark before creation so partial clusters remain addressable for cleanup.
                    self.state["cluster_created"] = True
                    self.state["stopped"] = False
                    self.save()
                    self.cluster.create()
            else:
                with self.step("start existing environment"):
                    self.cluster.start()
                    self.state["stopped"] = False
                if not self.state.get("bootstrapped"):
                    with self.step("load baseline AWX image"):
                        self.cluster.load_image(self.state["image"])
            if not self.state.get("bootstrapped"):
                with self.step("deploy AWX"):
                    self.cluster.deploy()
                    self.state["bootstrapped"] = True
            elif self.state.get("pending_update"):
                with self.step("finish pending AWX update"):
                    self.apply_update(self.state["pending_update"])
            else:
                with self.step("verify existing AWX deployment"):
                    self.cluster.wait_ready(self.state["image"])
            self.run_checks("smoke")
        return self.operation("up", start)

    def refresh(self, pr=None, plugins_pr=None, awx_ref=None, suite="smoke", no_cache=False, local=None):
        if not self.state.get("cluster_created") or not self.state.get("build") or not self.state.get("bootstrapped"):
            raise LabError("Create the environment first with 'awx-lab up'.")
        if self.state.get("stopped"):
            raise LabError("The environment is stopped. Run 'awx-lab up' before testing.")
        if self.state["architecture"] != architecture():
            raise LabError("The environment uses a different architecture; create a new environment.")
        selection = {"pr": pr, "plugins_pr": plugins_pr,
                     "awx_ref": awx_ref or self.state["settings"]["awx_ref"]}
        if local:
            selection["local"] = local.selection()
        label = f"local {local.repo} branch {local.branch!r} in {local.path}" if local else pr
        self.runner.write(f"Testing {label}\nDetailed output: awx-lab logs --follow\n", level="progress")
        def test():
            with self.step("prerequisites"):
                self.prerequisites()
                self.cluster.require_existing()
            with self.step("snapshot local sources" if local else "fetch and merge"):
                self.state.pop("requested_sources", None)
                self.state["requested_selection"] = selection
                candidate = self.prepare_sources(selection)
                candidate["awx-operator"] = self.state["sources"]["awx-operator"]
                self.state["requested_sources"] = candidate
                if local:
                    snapshot = candidate[local.repo]
                    detail = "includes uncommitted changes" if snapshot["dirty"] else "matches HEAD"
                    self.runner.write(f"Local snapshot: {snapshot['fingerprint'][:12]} ({detail}).\n", level="progress")
            self.state["source_changed"] = candidate != self.state["sources"]
            if self.state["source_changed"] or no_cache or self.state.get("pending_update"):
                image = f"localhost/awx-lab:{self.state['id']}-{uuid.uuid4().hex[:8]}"
                with self.step("build updated AWX image"):
                    settings = self.state["settings"]
                    builder = Builder(self.runner, self.state["runtime"], self.path, self.state["architecture"],
                                      settings["build_timeout"], record_image=self.track_image)
                    build = builder.build(image, plugin="awx-plugins" in candidate,
                                          no_cache=no_cache, headless=settings["headless"])
                update = {"image": image, "sources": candidate, "build": build, "selection": selection}
                self.state["pending_update"] = update
                self.save()
                with self.step("update existing AWX deployment"):
                    self.apply_update(update)
            else:
                self.runner.write("Selected sources are unchanged; skipping image build and deployment.\n", level="progress")
                with self.step("verify existing AWX deployment"):
                    self.cluster.wait_ready(self.state["image"])
                self.state["selection"] = selection
            self.run_checks(suite)
        return self.operation("test", test)

    def stop(self):
        if self.state["cluster_created"]:
            try:
                self.cluster.stop()
            except (Exception, KeyboardInterrupt) as error:
                self.state.update(status="stop_failed", error=str(error) or "Interrupted while stopping")
                self.save()
                raise
        self.state.update(status="stopped", stopped=True)
        self.save()
        self.runner.write("Environment stopped. Database and files retained; run 'awx-lab up' to resume.\n", level="result")
        return 0

    def purge(self):
        # Remove the transient helper left by an interrupted build, if any.
        self.runner.run([self.state["runtime"], "rm", "--force", self.state["id"] + "-tools"],
                        capture=True, check=False, timeout=30)
        if self.state["cluster_created"]:
            self.cluster.delete()
            self.state.update(cluster_created=False, bootstrapped=False, status="destroyed", stopped=True)
            self.save()
        images = list(dict.fromkeys([*self.state.get("images", []), self.state["image"]]))
        prefixes = (f"localhost/awx-lab:{self.state['id']}", f"localhost/awx-lab-ui:{self.state['id']}")
        if any(not any(image == prefix or image.startswith(prefix + "-") for prefix in prefixes) for image in images):
            raise LabError("Refusing to purge images not owned by this environment; inspect its saved state.")
        present = set(self.runner.run([self.state["runtime"], "image", "ls", "--format",
                                      "{{.Repository}}:{{.Tag}}"], capture=True).stdout.splitlines())
        for image in reversed(images):
            if image in present:
                self.runner.run([self.state["runtime"], "image", "rm", image], capture=True)
        shutil.rmtree(self.path)
        self.runner.log = None  # The workspace containing the log has been deleted.
        self.runner.write("Environment destroyed; database, lab image tags and saved files removed.\n", level="result")
        return 0
