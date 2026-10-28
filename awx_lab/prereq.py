"""Inspect, approve, install, then verify local development prerequisites."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from .system import LabError, Runner, architecture, choose_runtime, doctor, managed_bin_dir, print_doctor


KIND_VERSION = "v0.33.0"
# Match the Kubernetes 1.34 minor used by the default lab node image.
KUBECTL_VERSION = "v1.34.11"


@dataclass
class Action:
    description: str
    command: list[str] = field(default_factory=list)
    tool: str | None = None
    url: str | None = None
    checksum_url: str | None = None


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def command(self, description, *command):
        self.actions.append(Action(description, list(command)))


def binary_action(tool, system, arch):
    if tool == "kind":
        url = f"https://github.com/kubernetes-sigs/kind/releases/download/{KIND_VERSION}/kind-{system.lower()}-{arch}"
        checksum_url = url + ".sha256sum"
        version = KIND_VERSION
    else:
        url = f"https://dl.k8s.io/release/{KUBECTL_VERSION}/bin/{system.lower()}/{arch}/kubectl"
        checksum_url = url + ".sha256"
        version = KUBECTL_VERSION
    return Action(f"Install {tool} {version} ({system.lower()}/{arch}) in {managed_bin_dir()}",
                  tool=tool, url=url, checksum_url=checksum_url)


def privilege_prefix(plan):
    if os.geteuid() == 0:
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    plan.blockers.append("Installing system packages needs sudo or a root shell. User-level kind/kubectl downloads do not need sudo.")
    return []


def plan_packages(plan, missing, system, runtime):
    packages = [name for name in ("git", runtime) if name in missing]
    if not packages:
        return
    if system == "Darwin":
        brew = shutil.which("brew")
        if not brew:
            plan.blockers.append("Homebrew is required to install missing Git/container runtime packages on macOS. Install it from https://brew.sh, then rerun this command.")
            return
        formulae = [name for name in packages if name != "docker"]
        if formulae:
            plan.command("Install missing packages using Homebrew: " + ", ".join(formulae), brew, "install", *formulae)
        if "docker" in packages:
            plan.command("Install Docker Desktop (its first launch may need GUI setup)", brew, "install", "--cask", "docker")
        return
    prefix = privilege_prefix(plan)
    if shutil.which("dnf"):
        if "docker" in packages:
            plan.blockers.append("Automatic Docker installation on this Linux distribution is not supported. Use --runtime podman or install Docker Engine first.")
            return
        plan.command("Install missing system packages: " + ", ".join(packages), *prefix, "dnf", "install", "-y", *packages)
    elif shutil.which("apt-get"):
        packages = ["docker.io" if name == "docker" else name for name in packages]
        plan.command("Refresh APT package indexes", *prefix, "apt-get", "update")
        plan.command("Install missing system packages: " + ", ".join(packages), *prefix, "apt-get", "install", "-y", *packages)
    else:
        plan.blockers.append("Automatic system package installation supports dnf and apt-get on Linux. Install " + ", ".join(packages) + " with your distribution's package manager, then rerun.")


def read_json_command(runner, command):
    result = runner.run(command, capture=True, timeout=30)
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, list) or any(not isinstance(item, dict) or not isinstance(item.get("Name"), str) for item in value):
            raise ValueError("expected a list of named machines/connections")
        return value
    except ValueError as error:
        raise LabError(f"Cannot read {' '.join(command)}: {error}") from error


def plan_podman_machine(plan, args, runner, installed, connected):
    if os.environ.get("CONTAINER_HOST") or os.environ.get("CONTAINER_CONNECTION"):
        if not connected:
            plan.blockers.append("Podman is using a connection override. Restore that connection or unset CONTAINER_HOST/CONTAINER_CONNECTION before automatic VM setup.")
        return
    if connected and not args.machine:
        return
    try:
        machines = read_json_command(runner, ["podman", "machine", "list", "--format", "json"]) if installed else []
        connections = read_json_command(runner, ["podman", "system", "connection", "list", "--format", "json"]) if installed else []
    except LabError as error:
        plan.blockers.append(str(error))
        return
    default = next((connection for connection in connections if connection.get("Default")), None)
    if default and not default.get("IsMachine") and not args.machine:
        plan.blockers.append("The default Podman connection is not a local machine. Restore it or select a local VM with --machine NAME.")
        return
    selected = None
    if args.machine:
        selected = next((machine for machine in machines if machine["Name"] == args.machine), None)
    elif default:
        selected = next((machine for machine in machines if default["Name"] in (machine["Name"], machine["Name"] + "-root")), None)
        if selected is None:
            plan.blockers.append("The configured Podman connection does not match a local machine. Select one with --machine NAME.")
            return
    elif machines:
        defaults = [machine for machine in machines if machine.get("Default")]
        if len(defaults) == 1:
            selected = defaults[0]
        elif len(machines) == 1:
            selected = machines[0]
        else:
            plan.blockers.append("Several Podman machines exist with no clear default. Select one using --machine NAME.")
            return
    name = selected["Name"] if selected else (args.machine or "awx-lab")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        plan.blockers.append("Invalid Podman machine name.")
        return
    if any(machine.get("Running") or machine.get("Starting") for machine in machines if machine["Name"] != name):
        plan.blockers.append("Another Podman VM is running. Stop it yourself before starting the selected VM; the installer will not interrupt it.")
        return
    if selected and selected.get("Starting"):
        plan.blockers.append(f"Podman VM {name} is already starting. Wait for it, then rerun.")
        return
    if selected is None:
        plan.command(f"Create Podman VM {name}: {args.cpus} CPUs, {args.memory} MiB RAM, {args.disk_size} GiB disk",
                     "podman", "machine", "init", "--cpus", str(args.cpus), "--memory", str(args.memory),
                     "--disk-size", str(args.disk_size), name)
    elif not selected.get("Running"):
        try:
            memory = int(selected.get("Memory", 0)) // (1024 * 1024)
        except (TypeError, ValueError):
            memory = 0
        if memory and memory < 8192 and args.memory > memory:
            plan.command(f"Increase stopped VM {name} memory from {memory} to {args.memory} MiB",
                         "podman", "machine", "set", "--memory", str(args.memory), name)
    if default is None or default["Name"] not in (name, name + "-root"):
        plan.command(f"Select {name} as the default Podman connection", "podman", "system", "connection", "default", name)
    if selected is None or not selected.get("Running"):
        plan.command(f"Start Podman VM {name}", "podman", "machine", "start", name)
    elif not connected:
        plan.notes.append(f"VM {name} is already running. The final doctor check will verify its connection; no restart is scheduled.")


def make_plan(report, args, *, runner=None, system=None):
    runner = runner or Runner()
    system = system or platform.system()
    plan = Plan()
    missing = {item["name"] for item in report["checks"] if item["status"] == "fail"}
    if system not in ("Darwin", "Linux"):
        plan.blockers.append("The prerequisite installer supports macOS and Linux.")
        return plan
    try:
        arch = architecture()
    except LabError as error:
        plan.blockers.append(str(error))
        return plan
    for name in missing - {"git", "kind", "kubectl", "podman", "docker", "runtime connection"}:
        detail = next(item["detail"] for item in report["checks"] if item["name"] == name)
        plan.blockers.append(f"{name} needs manual configuration: {detail}")
    for tool in ("kind", "kubectl"):
        if tool in missing:
            plan.actions.append(binary_action(tool, system, arch))
    runtime = report["runtime"]
    plan_packages(plan, missing, system, runtime)
    connected = any(item["name"] == "runtime connection" and item["status"] == "ok" for item in report["checks"])
    if runtime == "podman" and system == "Darwin":
        plan_podman_machine(plan, args, runner, runtime not in missing, connected)
    elif runtime == "docker" and not connected:
        if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
            plan.blockers.append("Restore the configured Docker endpoint or unset DOCKER_HOST/DOCKER_CONTEXT before automatic startup.")
        elif system == "Darwin":
            plan.command("Start Docker Desktop; complete any first-launch GUI prompts", "open", "-a", "Docker")
        elif shutil.which("systemctl"):
            plan.command("Start the Docker system service", *privilege_prefix(plan), "systemctl", "start", "docker")
            plan.notes.append("Docker must be accessible to your user. If socket permissions fail, configure rootless Docker or use Podman; this command does not change group membership.")
        else:
            plan.blockers.append("Start Docker using your system's service manager, then rerun.")
    elif runtime == "podman" and runtime not in missing and not connected:
        plan.blockers.append("Podman on Linux runs without a VM or daemon. Resolve the reported podman info error (connection, permissions or cgroups), then rerun.")
    return plan


def install_binary(action):
    """Verify a temporary download before atomically installing its executable."""
    directory = managed_bin_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / action.tool
    if destination.exists() or destination.is_symlink():
        raise LabError(f"Refusing to replace existing {destination}; inspect/remove it before retrying.")
    try:
        request = urllib.request.Request(action.checksum_url, headers={"User-Agent": "awx-lab"})
        with urllib.request.urlopen(request, timeout=30) as response:
            expected = response.read(4096).decode().split()[0].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise LabError(f"Invalid SHA256 checksum for {action.tool}.")
        with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{action.tool}-", delete=False) as handle:
            temporary = Path(handle.name)
            try:
                digest = hashlib.sha256()
                request = urllib.request.Request(action.url, headers={"User-Agent": "awx-lab"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        handle.write(chunk)
                handle.flush()
                if digest.hexdigest() != expected:
                    raise LabError(f"SHA256 mismatch for {action.tool}; the executable was not installed.")
                temporary.chmod(0o755)
                # link fails on a concurrent install instead of overwriting another executable.
                os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    except (urllib.error.URLError, OSError, ValueError, IndexError) as error:
        raise LabError(f"Could not install {action.tool}: {error}") from error


def execute_command(command):
    print("$ " + shlex.join(command), flush=True)
    env = os.environ.copy()
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        env.pop(key, None)
    # Inherit the terminal so sudo/package-manager prompts work normally.
    # A conventional umask prevents system packages inheriting the private run-data mask.
    try:
        result = subprocess.run(command, env=env, timeout=1800, umask=0o022)
    except subprocess.TimeoutExpired as error:
        raise LabError("Installation command timed out; inspect the package manager/runtime before retrying.") from error
    if result.returncode:
        raise LabError(f"Installation command failed ({result.returncode}): {shlex.join(command)}")


def confirm():
    while True:
        try:
            answer = input("Install/configure these prerequisites? [Y/n] ").strip().lower()
        except EOFError:
            print("\nNo approval received; nothing changed.")
            return False
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Enter Y or n.")


def install_prerequisites(args, state_root):
    if args.memory < 8192:
        raise LabError("Use at least --memory 8192 MiB for AWX's Podman VM.")
    runtime = choose_runtime(args.runtime)
    print("Checking prerequisites…")
    before = doctor(runtime, state_root)
    print_doctor(before)
    plan = make_plan(before, args)
    if plan.actions:
        print("\nProposed changes:")
        for index, action in enumerate(plan.actions, 1):
            print(f"  {index}. {action.description}")
            if action.command:
                print("     " + shlex.join(action.command))
            else:
                print(f"     {action.url}\n     Verify SHA256 using {action.checksum_url}")
    for note in plan.notes:
        print("Note: " + note)
    if plan.blockers:
        print("\nResolve these prerequisites before installation:")
        for blocker in plan.blockers:
            print("  - " + blocker)
        print("No changes made.")
        return 1
    if not plan.actions:
        print("\nAll required checks pass; nothing to install." if before["ok"] else "\nNo automatic fix is available for the failed checks.")
        if any(item["status"] == "warn" for item in before["checks"]):
            print("Review the resource warnings above before building AWX.")
        return 0 if before["ok"] else 1
    if args.dry_run:
        print("\nDry run: nothing installed or started.")
        return 0
    if not confirm():
        print("Cancelled; nothing changed.")
        return 0
    failure = None
    try:
        for action in plan.actions:
            print("\n" + action.description, flush=True)
            if action.tool:
                install_binary(action)
            else:
                execute_command(action.command)
    except (LabError, OSError, KeyboardInterrupt) as error:
        failure = error
        print(f"\nSetup stopped: {error or 'interrupted by user'}. Completed steps are retained; rerunning checks them again.")
    print("\nVerifying prerequisites…")
    after = doctor(runtime, state_root)
    # Docker Desktop's open command returns before its engine becomes ready.
    if not failure and runtime == "docker" and any(action.command == ["open", "-a", "Docker"] for action in plan.actions):
        deadline = time.monotonic() + 120
        while not after["ok"] and time.monotonic() < deadline:
            time.sleep(3)
            after = doctor(runtime, state_root)
    print_doctor(after)
    if failure or not after["ok"]:
        print("\nPrerequisite setup is incomplete. Resolve the errors above and rerun awx-lab prereq install.")
        return 130 if isinstance(failure, KeyboardInterrupt) else 1
    print("\nPrerequisites verified. Run awx-lab up, then awx-lab test <PR_URL> or awx-lab test --local from your source checkout.")
    if any(item["status"] == "warn" for item in after["checks"]):
        print("Resource warnings remain; review them before building AWX.")
    return 0
