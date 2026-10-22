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
    def __init__(self, log: Path | None = None, env=None):
        self.log = log
        self.env = dict(env or {})

    def write(self, value, echo=True):
        if echo:
            print(value, end="", flush=True)
        if self.log:
            with self.log.open("a") as handle:
                handle.write(value)

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
            raise CommandError(command, process.returncode, output)
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
        raise LabError("Invalid run ID. Use an ID printed by 'awx-lab status'.")
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
            raise LabError("This run is busy in another terminal; wait or cancel that command first.") from None
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
    return "podman" if shutil.which("podman") else "docker"


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
            info = Runner().run([runtime, "info", "--format", "json" if runtime == "podman" else "{{json .}}"],
                                timeout=30, capture=True)
            data = json.loads(info.stdout)
            host = data.get("host", data)
            runtime_arch = architecture(host.get("arch", host.get("Architecture", "")))
            add("runtime connection", True, f"{runtime}: {runtime_arch}")
            add("native runtime", runtime_arch == arch, f"host={arch}, runtime={runtime_arch}; use a native runtime VM")
            memory = host.get("memTotal", host.get("MemTotal", 0))
            if memory:
                add("runtime RAM", memory >= 8 * 1024**3,
                    f"{memory / 1024**3:.1f} GiB; start with 12–16 GiB for a single lab (estimate)", warning=True)
            if runtime == "podman" and host.get("security", {}).get("rootless"):
                add("rootless cgroups", host.get("cgroupVersion") == "v2", "kind requires cgroup v2 and delegated controllers")
        except (LabError, ValueError) as error:
            add("runtime connection", False, f"{error}\nStart your container runtime; for Podman on macOS: podman machine start")
    existing = state_root
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free / 1024**3
    add("workspace disk", free >= 40, f"{free:.1f} GiB free; allow 40+ GiB, plus runtime VM storage", warning=True)
    return {"runtime": runtime, "checks": checks, "ok": all(c["status"] != "fail" for c in checks)}
