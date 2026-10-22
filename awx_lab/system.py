"""Process execution, private run state and host prerequisite checks."""

from __future__ import annotations

import contextlib
from collections import deque
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import time


class LabError(Exception):
    pass


class CommandError(LabError):
    def __init__(self, command, returncode, output):
        self.returncode = returncode
        self.output = output
        super().__init__(f"Command failed ({returncode}): {shlex.join(map(str, command))}\n{output[-2000:]}")


LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3


def rotate_log(path):
    files = [path, *(path.with_name(f"{path.name}.{i}") for i in range(1, LOG_BACKUPS + 1))]
    if any(p.is_symlink() for p in files):
        raise LabError("Refusing a symlink as a log file.")
    # Older releases could leave an unlimited log. Bound its retained tail too.
    if path.stat().st_size > LOG_MAX_BYTES:
        with path.open("rb") as handle:
            handle.seek(-LOG_MAX_BYTES, os.SEEK_END)
            tail = handle.read().decode("utf-8", errors="ignore").encode("utf-8")
        path.write_bytes(tail)
    files[-1].unlink(missing_ok=True)
    for source, destination in reversed(list(zip(files, files[1:]))):
        if source.exists():
            source.replace(destination)


def read_log(path, follow=False):
    while True:
        try:
            handle = path.open("rb")
        except FileNotFoundError:
            if not follow:
                raise
            time.sleep(0.3)
            continue
        with handle:
            while True:
                data = handle.read()
                if data:
                    yield data.decode("utf-8", errors="replace")
                    if follow:
                        continue  # Drain the old file before switching after rotation.
                if not follow:
                    return
                try:
                    current, opened = path.stat(), os.fstat(handle.fileno())
                    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino) or current.st_size < handle.tell():
                        break
                except FileNotFoundError:
                    pass
                time.sleep(0.3)


def atomic_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def architecture(value=None):
    value = (value or platform.machine()).lower()
    try:
        return {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}[value]
    except KeyError:
        raise LabError(f"Unsupported architecture: {value}; use ARM64 or x86_64.") from None


class Runner:
    def __init__(self, log: Path | None = None, env=None, *, log_mode="full"):
        self.log = log
        self.env = dict(env or {})
        self.log_mode = log_mode

    def write(self, value, echo=True, *, level="detail"):
        visible = (self.log_mode == "full" or level == "error" or
                   (self.log_mode == "clean" and level in ("progress", "result", "warning")))
        if echo and visible:
            print(value, end="", flush=True, file=sys.stderr if level == "error" else sys.stdout)
        if self.log:
            if self.log.is_symlink():
                raise LabError("Refusing a symlink as a log file.")
            # Even a single very large write must respect the byte limit. Splitting
            # by characters keeps each rotated file valid UTF-8.
            chunk_size = max(1, LOG_MAX_BYTES // 4)
            for start in range(0, len(value), chunk_size):
                data = value[start:start + chunk_size].encode("utf-8")
                if self.log.exists() and self.log.stat().st_size + len(data) > LOG_MAX_BYTES:
                    rotate_log(self.log)
                with self.log.open("ab") as handle:
                    handle.write(data)

    def run(self, command, *, cwd=None, timeout=900, capture=False, check=True, env=None, sensitive=False):
        command = list(map(str, command))
        self.write(f"$ {shlex.join(command)}\n", echo=not capture)
        child_env = {**os.environ, **self.env, **(env or {})}
        child_env.pop("GITHUB_TOKEN", None)
        child_env.pop("GH_TOKEN", None)
        try:
            process = subprocess.Popen(command, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as error:
            raise LabError(f"Cannot run {command[0]}: {error}") from error
        chunks = [] if capture else deque(maxlen=64)
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise LabError(f"Timed out after {timeout}s: {shlex.join(command)}")
                    for key, _ in selector.select(timeout=0.2):
                        data = os.read(key.fileobj.fileno(), 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        value = data.decode("utf-8", errors="replace")
                        chunks.append(value)
                        if not sensitive:
                            self.write(value, echo=not capture)
                process.wait(timeout=max(1, deadline - time.monotonic()))
        except BaseException:
            self.stop(process)
            raise
        finally:
            process.stdout.close()
        output = "".join(chunks)
        if check and process.returncode:
            raise CommandError(command, process.returncode, "[sensitive output hidden]" if sensitive else output)
        return subprocess.CompletedProcess(command, process.returncode, output)

    @staticmethod
    def stop(process):
        # Stop descendants too (git helpers, build clients, port forwarding).
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


RUN_ID = re.compile(r"awx-lab-[a-z0-9-]{1,45}\Z")


def run_path(root: Path, run_id: str):
    if not RUN_ID.fullmatch(run_id):
        raise LabError("Invalid legacy run ID. Use an ID printed by 'awx-lab status --all'.")
    path = root / run_id
    if path.is_symlink() or not path.is_dir():
        raise LabError(f"Unknown run: {run_id}")
    return path


@contextlib.contextmanager
def lock(path: Path):
    with (path / ".lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LabError("The environment is busy in another terminal; wait or cancel that command first.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def choose_runtime(requested):
    if requested != "auto":
        return requested
    for name in ("podman", "docker"):
        if shutil.which(name):
            try:
                result = subprocess.run([name, "info"], capture_output=True, timeout=15)
                if result.returncode == 0:
                    return name
            except (OSError, subprocess.TimeoutExpired):
                pass
    return "docker" if shutil.which("docker") and not shutil.which("podman") else "podman"


def managed_bin_dir():
    return Path.home() / ".local/share/awx-lab/bin"


def configure_tools_path():
    """Make installer-managed tools available without changing shell profiles."""
    directory = str(managed_bin_dir())
    paths = os.environ.get("PATH", "").split(os.pathsep)
    if directory not in paths:
        os.environ["PATH"] = os.pathsep.join([*paths, directory])


def print_doctor(result):
    for check in result["checks"]:
        print(f"{check['status'].upper():4} {check['name']}: {check['detail']}")


def podman_connection_status(runner):
    """Explain the selected macOS endpoint without changing runtime settings."""
    overrides = [name for name in ("CONTAINER_HOST", "CONTAINER_CONNECTION", "CONTAINER_SSHKEY") if os.environ.get(name)]
    if overrides:
        return False, ("Podman connection overrides are set: " + ", ".join(overrides) +
                       ". Check those settings and 'podman system connection list'; the local VM may not be the selected endpoint.")
    fallback = "Inspect 'podman machine list' and 'podman system connection list' to check the selected VM and endpoint."
    try:
        connections = json.loads(runner.run(["podman", "system", "connection", "list", "--format", "json"],
                                            capture=True, timeout=5).stdout)
        if not isinstance(connections, list) or any(not isinstance(item, dict) for item in connections):
            return False, fallback
        connection = next((item for item in connections if item.get("Default")), None)
        if not connection or connection.get("IsMachine") is False:
            return False, "The selected Podman endpoint is not a confirmed local VM. " + fallback
        machines = json.loads(runner.run(["podman", "machine", "list", "--format", "json"], capture=True, timeout=5).stdout)
        if not isinstance(machines, list) or any(not isinstance(item, dict) for item in machines):
            return False, fallback
        machine = next((item for item in machines if isinstance(item.get("Name"), str) and
                        connection.get("Name") in (item["Name"], item["Name"] + "-root")), None)
        if not machine:
            return False, "The selected connection does not match a local Podman VM. " + fallback
        name = machine["Name"]
        if machine.get("Running") or machine.get("Starting"):
            state = "starting" if machine.get("Starting") else "running"
            probe = shlex.join(["podman", "--connection", connection["Name"], "info"])
            ssh = shlex.join(["podman", "machine", "ssh", name, "true"])
            return True, (f"Podman reports VM {name} as {state}, but its API is unavailable. "
                          f"Check '{probe}' and '{ssh}' for connection or guest-service errors.")
        start = shlex.join(["podman", "machine", "start", name])
        return False, f"Podman VM {name} is stopped. Run '{start}', then retry."
    except (LabError, ValueError, TypeError, KeyError):
        return False, fallback


def runtime_info(runner, runtime):
    command = [runtime, "info", "--format", "json" if runtime == "podman" else "{{json .}}"]
    if runtime != "podman" or platform.system() != "Darwin":
        try:
            return runner.run(command, timeout=30, capture=True), 1
        except LabError as error:
            raise LabError(f"{error}\nCheck the selected {runtime} runtime and its connection settings.") from error
    # A VM can report Running before SSH and Podman's API are usable. Retry only
    # transient connection failures against a confirmed running/starting local VM.
    deadline = time.monotonic() + 30
    status = None
    for attempt in range(1, 6):
        try:
            return runner.run(command, timeout=max(0.1, min(10, deadline - time.monotonic())), capture=True), attempt
        except LabError as error:
            if status is None:
                status = podman_connection_status(runner)
            text = str(error).lower()
            transient = any(marker in text for marker in (
                "connection reset by peer", "connection refused", "unexpected eof", "handshake failed: eof",
                "connection timed out", "i/o timeout", "timed out after"))
            if not status[0] or not transient or attempt == 5 or time.monotonic() + 2 >= deadline:
                raise LabError(f"{error}\n{status[1]}") from error
            time.sleep(2)


def doctor(runtime, state_root: Path):
    checks = []

    def add(name, ok, detail, warning=False):
        checks.append({"name": name, "status": "ok" if ok else "warn" if warning else "fail", "detail": detail})

    add("Python", sys.version_info >= (3, 11), platform.python_version())
    add("OS", platform.system() in ("Darwin", "Linux"), platform.system())
    try:
        arch = architecture()
        add("architecture", True, arch)
    except LabError as error:
        arch = None
        add("architecture", False, str(error))
    for name in ("git", "kind", "kubectl", runtime):
        location = shutil.which(name)
        add(name, bool(location), location or f"Install {name} and add it to PATH.")
    if shutil.which(runtime):
        try:
            info, attempts = runtime_info(Runner(), runtime)
            data = json.loads(info.stdout)
            if not isinstance(data, dict):
                raise ValueError("The runtime returned an unexpected info response; expected a JSON object.")
            host = data.get("host", data)
            runtime_arch = architecture(host.get("arch", host.get("Architecture", "")))
            retry_note = f"; API became ready after {attempts} attempts" if attempts > 1 else ""
            add("runtime connection", True, f"{runtime}: {runtime_arch}{retry_note}")
            add("native runtime", runtime_arch == arch, f"host={arch}, runtime={runtime_arch}; use a native runtime VM")
            memory = host.get("memTotal", host.get("MemTotal", 0))
            if memory:
                # Linux excludes reserved memory from MemTotal. Allow 512 MiB
                # of overhead for a VM configured with the 8 GiB minimum.
                minimum = 8 * 1024**3 - 512 * 1024**2
                detail = (f"{memory / 1024**3:.1f} GiB total reported by {runtime}; "
                          "allocate at least 8 GiB to the container runtime, with 12–16 GiB recommended for one lab")
                if memory < minimum:
                    if runtime == "podman" and platform.system() == "Darwin":
                        detail += ". Stop your Podman VM, then run awx-lab prereq install --runtime podman to resize it"
                    add("runtime RAM", False, detail)
                else:
                    add("runtime RAM", memory >= 12 * 1024**3 - 512 * 1024**2, detail, warning=True)
            if runtime == "podman" and host.get("security", {}).get("rootless"):
                add("rootless cgroups", host.get("cgroupVersion") == "v2", "kind requires cgroup v2 and delegated controllers")
        except (LabError, ValueError) as error:
            add("runtime connection", False, str(error))
    existing = state_root
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free / 1024**3
    add("workspace disk", free >= 40, f"{free:.1f} GiB free; allow 40+ GiB, plus runtime VM storage", warning=True)
    return {"runtime": runtime, "checks": checks, "ok": all(c["status"] != "fail" for c in checks)}
