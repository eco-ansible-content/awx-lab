"""Installed CLI entry point; third-party build tools run inside a helper image."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

from . import __version__
from .cluster import Cluster
from .lab import Lab, now
from .source import PullRequest
from .system import LabError, Runner, architecture, atomic_json, choose_runtime, doctor, lock, run_path


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser():
    root = argparse.ArgumentParser(prog="awx-lab", description="Build and test AWX PRs in disposable local kind clusters.")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--state-dir", type=Path, default=Path(os.environ.get("AWX_LAB_HOME", "~/.local/share/awx-lab/runs")).expanduser(),
                      help="run storage (default: ~/.local/share/awx-lab/runs; also AWX_LAB_HOME)")
    commands = root.add_subparsers(dest="command", required=True)
    doc = commands.add_parser("doctor", help="check dependencies and the selected runtime without changing it")
    doc.add_argument("--runtime", choices=("auto", "podman", "docker"), default="auto")
    doc.add_argument("--json", action="store_true")
    for name in ("plan", "up", "test"):
        command = commands.add_parser(name, help={"plan": "show the run configuration without executing code",
                                                  "up": "build and retain a lab for manual testing",
                                                  "test": "build, run smoke checks and clean up"}[name])
        command.add_argument("pr", help="GitHub AWX or awx-plugins PR URL")
        command.add_argument("--plugins-pr", help="also include this awx-plugins PR in an AWX PR run")
        command.add_argument("--awx-ref", default="devel", help="AWX branch/tag/SHA when testing a plugin PR")
        command.add_argument("--runtime", choices=("auto", "podman", "docker"), default="auto")
        command.add_argument("--operator-version", default="2.19.1", help="operator release tag and image tag")
        command.add_argument("--node-image", default="kindest/node:v1.34.0")
        command.add_argument("--ee-image", default="quay.io/ansible/awx-ee:24.6.1", help="execution environment with native architecture support")
        command.add_argument("--port", type=positive, default=0, help="loopback HTTP port; otherwise choose a free port")
        command.add_argument("--headless", action="store_true", help="skip the AWX UI build; API-only lab")
        command.add_argument("--no-cache", action="store_true", help="rebuild the AWX image without layer cache")
        command.add_argument("--build-timeout", type=positive, default=7200, metavar="SECONDS")
        command.add_argument("--ready-timeout", type=positive, default=900, metavar="SECONDS")
        command.add_argument("--suite", choices=("smoke", "job"), default="smoke", help="job adds an Ansible localhost ping to test runs")
        command.add_argument("--keep", action="store_true", help="retain the cluster after a test, including failures")
        if name == "plan":
            command.add_argument("--json", action="store_true")
    status = commands.add_parser("status", help="show saved run state (not a live health check)")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--json", action="store_true")
    logs = commands.add_parser("logs", help="read the streamed build and lifecycle log")
    logs.add_argument("run_id")
    logs.add_argument("--follow", action="store_true")
    credentials = commands.add_parser("credentials", help="read the lab's generated admin credentials")
    credentials.add_argument("run_id")
    down = commands.add_parser("down", help="delete only this run's cluster and database")
    down.add_argument("run_id")
    down.add_argument("--purge", action="store_true", help="also delete this run's image, sources, logs and reports")
    return root


def validate_options(args):
    primary = PullRequest.parse(args.pr)
    if args.plugins_pr:
        plugin = PullRequest.parse(args.plugins_pr)
        if primary.repo != "awx" or plugin.repo != "awx-plugins":
            raise LabError("--plugins-pr requires an AWX primary PR and an awx-plugins companion PR.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._-]*)", args.operator_version):
        raise LabError("--operator-version must be a release tag, for example 2.19.1.")
    for value in (args.ee_image, args.node_image):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]*", value):
            raise LabError("Invalid container image reference.")
    if args.port and not 1024 <= args.port <= 65535:
        raise LabError("Use an unprivileged port between 1024 and 65535.")


def load_state(path):
    try:
        state = json.loads((path / "state.json").read_text())
        if state["id"] != path.name or state["runtime"] not in ("podman", "docker"):
            raise ValueError("run identity mismatch")
        return state
    except (OSError, ValueError, KeyError) as error:
        raise LabError(f"Cannot read run state at {path}: {error}") from error


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    state_root = args.state_dir.expanduser().resolve()
    try:
        if args.command == "doctor":
            result = doctor(choose_runtime(args.runtime), state_root)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                for check in result["checks"]:
                    print(f"{check['status'].upper():4} {check['name']}: {check['detail']}")
            return 0 if result["ok"] else 1
        if args.command in ("plan", "up", "test"):
            validate_options(args)
            options = {k: v for k, v in vars(args).items() if k not in ("command", "state_dir", "json")}
            if args.command == "plan":
                result = {"architecture": architecture(), "options": options,
                          "steps": ["check prerequisites", "fetch PR and merge target branch", "build native AWX image",
                                    "create isolated kind cluster", "deploy AWX Operator and AWX", "API smoke checks",
                                    "optional Ansible ping job", "save reports; test cleans up unless --keep"],
                          "note": "No commands executed. Operator/AWX compatibility and supporting ARM images need an end-to-end validation."}
                print(json.dumps(result, indent=2) if args.json else "\n".join(result["steps"]) + "\n\n" + result["note"])
                return 0
            options["runtime"] = choose_runtime(args.runtime)
            return Lab(state_root, options).execute(args.command)
        if args.command == "status":
            paths = [run_path(state_root, args.run_id)] if args.run_id else sorted(state_root.glob("awx-lab-*"))
            states = [load_state(p) for p in paths if p.is_dir() and not p.is_symlink()]
            if args.json:
                print(json.dumps(states, indent=2))
            elif not states:
                print("No runs yet.")
            else:
                for state in states:
                    print(f"{state['id']}  {state['status']}  cluster={'retained' if state['cluster_created'] else 'absent'}  {state['url']}")
            return 0
        path = run_path(state_root, args.run_id)
        state = load_state(path)
        if args.command == "logs":
            with (path / "run.log").open() as handle:
                while True:
                    content = handle.read()
                    if content:
                        print(content, end="", flush=True)
                    if not args.follow:
                        break
                    time.sleep(0.3)
            return 0
        cluster = Cluster(Runner(), path, state)
        if args.command == "credentials":
            if not state["cluster_created"]:
                raise LabError("This run's cluster has already been removed.")
            username, password = cluster.credentials()
            print(f"URL: {state['url']}\nUsername: {username}\nPassword: {password}")
            return 0
        if args.command == "down":
            with lock(path):
                cluster.runner.run([state["runtime"], "rm", "--force", state["id"] + "-tools"], capture=True, check=False, timeout=30)
                if state["cluster_created"]:
                    cluster.delete()
                    state["cluster_created"] = False
                    state["status"] = "stopped"
                    state["updated_at"] = now()
                    atomic_json(path / "state.json", state)
                if args.purge:
                    result = cluster.runner.run([state["runtime"], "image", "rm", state["image"]], capture=True, check=False)
                    if result.returncode:
                        print("Image removal was unsuccessful (it may be absent or still in use); shared images/cache are retained.", file=sys.stderr)
                    shutil.rmtree(path)
            print(f"Removed {args.run_id}; " + ("run files purged." if args.purge else "logs and reports retained."))
            return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (LabError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0
