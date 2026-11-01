"""Installed CLI entry point; third-party build tools run inside a helper image."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from . import __version__
from .help_text import COMMAND_HELP, ROOT_DESCRIPTION, ROOT_EPILOG, HelpFormatter
from .lab import Lab
from .local_source import LocalSource
from .source import PullRequest
from .system import RUN_ID, LabError, architecture, atomic_json, choose_runtime, configure_tools_path, doctor, lock, print_doctor, read_log, run_path


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def add_log_flags(command):
    modes = command.add_mutually_exclusive_group()
    modes.add_argument("--error-log", dest="log_mode", action="store_const", const="error",
                       help="show only failures and their diagnostic context; silent on success")
    modes.add_argument("--full-log", dest="log_mode", action="store_const", const="full",
                       help="show all normal command output, including environment setup and builds")
    command.set_defaults(log_mode="clean")


def parser():
    root = argparse.ArgumentParser(prog="awx-lab", description=ROOT_DESCRIPTION, epilog=ROOT_EPILOG,
                                   formatter_class=HelpFormatter)
    root.add_argument("--version", action="version", version=__version__, help="show the installed CLI version and exit")
    root.add_argument("--state-dir", type=Path, metavar="PATH", default=Path(os.environ.get("AWX_LAB_HOME", "~/.local/share/awx-lab/runs")).expanduser(),
                      help="lab storage; place before the command (default: AWX_LAB_HOME or ~/.local/share/awx-lab/runs)")
    commands = root.add_subparsers(dest="command", title="commands", metavar="COMMAND")

    def command_parser(group, name, **kwargs):
        description, epilog = COMMAND_HELP[name]
        return group.add_parser(name, description=description, epilog=epilog,
                                formatter_class=HelpFormatter, **kwargs)

    runtime_help = "container runtime (default: auto, prefer reachable Podman, then Docker)"
    doc = command_parser(commands, "doctor", help="check dependencies and the selected runtime without changing it")
    doc.add_argument("--runtime", choices=("auto", "podman", "docker"), default="auto", help=runtime_help)
    doc.add_argument("--json", action="store_true", help="print check results as JSON")
    prereq = command_parser(commands, "prereq", help="manage host prerequisites")
    prereq_commands = prereq.add_subparsers(dest="prereq_command", required=True, title="commands", metavar="COMMAND")
    install = command_parser(prereq_commands, "install", help="check prerequisites, request Y/n approval, install and verify")
    install.add_argument("--runtime", choices=("auto", "podman", "docker"), default="auto", help=runtime_help)
    install.add_argument("--dry-run", action="store_true", help="show the proposed actions without installing or starting anything")
    install.add_argument("--machine", metavar="NAME", help="Podman machine to prepare on macOS; otherwise use the configured default")
    install.add_argument("--cpus", type=positive, default=6, metavar="N", help="CPUs for a new Podman VM (default: 6)")
    install.add_argument("--memory", type=positive, default=16384, metavar="MIB", help="memory for a new or undersized stopped Podman VM (minimum: 8192; default: 16384)")
    install.add_argument("--disk-size", type=positive, default=80, metavar="GIB", help="disk for a new Podman VM (default: 80)")
    for name in ("plan", "up", "test"):
        command = command_parser(commands, name, allow_abbrev=False, help={
            "plan": "preview baseline setup or a PR test without executing code",
            "up": "create the baseline environment or resume it",
            "test": "test a PR or local working changes in the existing environment"}[name])
        if name in ("up", "test"):
            add_log_flags(command)
        if name != "up":
            source = command.add_mutually_exclusive_group(required=True) if name == "test" else command
            source.add_argument("pr", nargs="?", metavar="PR_URL", help="open public GitHub AWX or awx-plugins PR URL; merged with its latest target")
            if name == "test":
                source.add_argument("--local", nargs="?", const="", metavar="BRANCH",
                                    help="test this checkout including uncommitted files; optional branch must be checked out")
            command.add_argument("--plugins-pr", metavar="URL",
                                 help="also include this awx-plugins PR; requires an AWX primary PR" +
                                 (", incompatible with --local" if name == "test" else ""))
            command.add_argument("--suite", choices=("smoke", "job"), default="smoke", help="checks to run (default: smoke API checks; job also runs an Ansible localhost ping)")
        ref_help = {
            "up": "baseline AWX branch/tag/SHA (default: %(default)s)",
            "plan": "baseline AWX branch/tag/SHA for creation or plugin tests (default: %(default)s)",
            "test": "AWX branch/tag/SHA for plugin tests only (default: environment's baseline ref)",
        }
        command.add_argument("--awx-ref", default=None if name == "test" else "devel", metavar="REF",
                             help=ref_help[name])
        command.add_argument("--no-cache", action="store_true", help="build without cached AWX/UI layers")
        if name != "test":
            command.add_argument("--runtime", choices=("auto", "podman", "docker"), default="auto", help=runtime_help)
            command.add_argument("--operator-version", default="devel", metavar="VERSION", help="operator release tag or devel; selects manifests and image (default: %(default)s)")
            command.add_argument("--node-image", default="kindest/node:v1.34.0", metavar="IMAGE", help="kind Kubernetes node image (default: %(default)s)")
            command.add_argument("--ee-image", default="quay.io/ansible/awx-ee:24.6.1", metavar="IMAGE", help="AWX execution environment image for jobs (default: %(default)s)")
            command.add_argument("--port", type=positive, default=0, metavar="PORT", help="loopback HTTP port, 1024-65535 (default: choose a free port)")
            command.add_argument("--headless", action="store_true", help="skip the AWX UI build; API-only lab (default: build the UI)")
            command.add_argument("--build-timeout", type=positive, default=7200, metavar="SECONDS", help="timeout per build/helper command, not the total run (default: %(default)s seconds)")
            command.add_argument("--ready-timeout", type=positive, default=900, metavar="SECONDS", help="timeout for deployment readiness waits (default: %(default)s seconds)")
        if name == "plan":
            command.add_argument("--json", action="store_true", help="print planned steps, architecture and options as JSON")
    status = command_parser(commands, "status", help="show saved environment state (not a live health check)")
    status.add_argument("--all", action="store_true", help="also list legacy environments and their internal IDs")
    status.add_argument("--json", action="store_true", help="print saved environment records as a JSON array")
    logs = command_parser(commands, "logs", help="read the environment's build and test log")
    logs.add_argument("--follow", action="store_true", help="keep streaming new log output across rotation; Ctrl-C to stop following")
    credentials = command_parser(commands, "credentials", help="read the environment's generated admin credentials")
    down = command_parser(commands, "down", allow_abbrev=False, help="stop the environment, keeping its database and disk state")
    add_log_flags(down)
    down.add_argument("--purge", action="store_true", help="destroy the environment, database, lab image tags and saved files")
    # Only retained for cleaning up pre-0.4 environments; the normal workflow needs no ID.
    for command in (logs, credentials, down):
        command.add_argument("run_id", nargs="?", help=argparse.SUPPRESS)
    return root


def validate_options(args):
    pr = getattr(args, "pr", None)
    primary = PullRequest.parse(pr) if pr else None
    if getattr(args, "plugins_pr", None):
        plugin = PullRequest.parse(args.plugins_pr)
        if not primary or primary.repo != "awx" or plugin.repo != "awx-plugins":
            raise LabError("--plugins-pr requires an AWX primary PR and an awx-plugins companion PR.")
    if args.awx_ref and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", args.awx_ref) or ".." in args.awx_ref):
        raise LabError("Invalid AWX ref; use a branch, tag or commit SHA.")
    if args.command == "test":
        if args.awx_ref and primary and primary.repo == "awx":
            raise LabError("--awx-ref only applies to plugin PRs; AWX PRs are merged with their own target branch.")
        return
    if args.operator_version != "devel" and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._-]*)", args.operator_version):
        raise LabError("--operator-version must be devel or a release tag, for example 2.19.1.")
    for value in (args.ee_image, args.node_image):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]*", value):
            raise LabError("Invalid container image reference.")
    if args.port and not 1024 <= args.port <= 65535:
        raise LabError("Use an unprivileged port between 1024 and 65535.")


def current_path(root):
    pointer = root / "current.json"
    if pointer.is_symlink():
        raise LabError("Refusing a symlink as the environment selection file.")
    if pointer.exists():
        try:
            identity = json.loads(pointer.read_text())["id"]
            if not isinstance(identity, str) or not RUN_ID.fullmatch(identity):
                raise ValueError("invalid environment identity")
            if not (root / identity).exists() and not (root / identity).is_symlink():
                # A purge may have finished deleting files before pointer removal.
                return None
            return run_path(root, identity)
        except (KeyError, TypeError, ValueError) as error:
            raise LabError("Cannot read the saved environment selection.") from error
    # Upgrade an unambiguous retained environment without creating another cluster.
    candidates = []
    for path in sorted(root.glob("awx-lab-*")):
        if path.is_dir() and not path.is_symlink() and load_state(path).get("cluster_created"):
            candidates.append(path)
    if len(candidates) > 1:
        raise LabError("Multiple legacy environments exist. Use 'awx-lab status --all' and 'awx-lab down <legacy-id> --purge' to remove the extras.")
    return candidates[0] if candidates else None


def require_current(root, legacy_id=None):
    path = run_path(root, legacy_id) if legacy_id else current_path(root)
    if path is None:
        raise LabError("No environment exists. Run 'awx-lab up' first.")
    return path


def validate_resume(args, argv, state):
    flags = {"--runtime", "--awx-ref", "--operator-version", "--node-image", "--ee-image", "--port",
             "--headless", "--build-timeout", "--ready-timeout"}
    for flag in flags.intersection(value.split("=", 1)[0] for value in argv):
        key = flag[2:].replace("-", "_")
        if key == "runtime" and args.runtime == "auto":
            continue
        saved = state[key] if key in ("runtime", "port") else state["settings"][key]
        if getattr(args, key) != saved:
            raise LabError(f"The existing environment keeps its original {flag}. Use 'awx-lab down --purge' before changing its configuration.")
    if args.no_cache and state.get("build"):
        raise LabError("'up' resumes the existing image. Use 'awx-lab test <PR_URL> --no-cache' or 'awx-lab test --local --no-cache' to rebuild it.")


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
    configure_tools_path()
    argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser().parse_args(argv)
    state_root = args.state_dir.expanduser().resolve()
    try:
        if args.command is None:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                parser().print_help()
                return 0
            from .tui import run
            return run(state_root)
        if args.command == "doctor":
            result = doctor(choose_runtime(args.runtime), state_root)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print_doctor(result)
            return 0 if result["ok"] else 1
        if args.command == "prereq":
            from .prereq import install_prerequisites
            return install_prerequisites(args, state_root)
        if args.command in ("plan", "up", "test"):
            validate_options(args)
        local = None
        if args.command == "test" and args.local is not None:
            local = LocalSource.inspect(Path.cwd(), args.local)
            if state_root.is_relative_to(local.path) or local.path.is_relative_to(state_root):
                raise LabError("The developer checkout and AWX Lab state directory must be separate; run --local from your own checkout.")
            if args.awx_ref and local.repo == "awx":
                raise LabError("--awx-ref only applies to plugin tests; --local already selects the AWX source.")
        if args.command == "plan":
            options = {k: v for k, v in vars(args).items() if k not in ("command", "state_dir", "json")}
            steps = (["require an existing running environment", "fetch PR and merge target branch",
                      "build and load updated image if sources changed", "update AWX in the existing cluster",
                      "wait for the requested image; run checks", "save reports and retain the environment"]
                     if args.pr else ["fetch baseline AWX ref", "build native baseline AWX image",
                                       "create kind cluster", "deploy AWX Operator, AWX and PostgreSQL",
                                       "run API smoke checks", "retain environment; later up calls resume it"])
            result = {"architecture": architecture(), "options": options, "steps": steps,
                      "note": "No commands executed. See docs/validation.md for platform acceptance status."}
            print(json.dumps(result, indent=2) if args.json else "\n".join(steps) + "\n\n" + result["note"])
            return 0
        if args.command == "status":
            if args.all:
                paths = [p for p in sorted(state_root.glob("awx-lab-*")) if p.is_dir() and not p.is_symlink()]
            else:
                path = current_path(state_root)
                paths = [path] if path else []
            states = [load_state(p) for p in paths]
            if args.json:
                print(json.dumps(states, indent=2))
            elif not states:
                print("No environment exists. Create one with 'awx-lab up'.")
            else:
                for state in states:
                    prefix = state["id"] + "  " if args.all else "Environment: "
                    print(f"{prefix}{state['status']}  {state['url']}")
            return 0
        if args.command == "logs":
            path = require_current(state_root, args.run_id)
            for content in read_log(path / "run.log", args.follow):
                print(content, end="", flush=True)
            return 0
        state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Serialize selection, creation and lifecycle changes across all terminals.
        with lock(state_root):
            if args.command == "up":
                path = current_path(state_root)
                creating = path is None
                if path is None:
                    options = {k: v for k, v in vars(args).items() if k not in ("command", "state_dir", "log_mode")}
                    options.update(runtime=choose_runtime(args.runtime), pr=None, plugins_pr=None)
                    lab = Lab(state_root, options, log_mode=args.log_mode)
                    atomic_json(state_root / "current.json", {"id": lab.state["id"]})
                    path = lab.path
                with lock(path):
                    state = load_state(path)
                    if not creating:
                        validate_resume(args, argv, state)
                        if args.no_cache:
                            state["settings"]["no_cache"] = True
                    atomic_json(state_root / "current.json", {"id": state["id"]})
                    return Lab.existing(path, state, log_mode=args.log_mode).up()
            if args.command == "down" and not args.run_id and current_path(state_root) is None:
                if args.log_mode != "error":
                    print("No environment exists; nothing to stop or destroy.")
                return 0
            path = require_current(state_root, getattr(args, "run_id", None))
            with lock(path):
                state = load_state(path)
                lab = Lab.existing(path, state, log_mode=getattr(args, "log_mode", "clean"))
                if args.command == "test":
                    options = {"plugins_pr": args.plugins_pr, "awx_ref": args.awx_ref,
                               "suite": args.suite, "no_cache": args.no_cache}
                    if local:
                        return lab.refresh(local=local, **options)
                    return lab.refresh(pr=PullRequest.parse(args.pr).url, **options)
                if args.command == "credentials":
                    if not state["cluster_created"] or state.get("stopped"):
                        raise LabError("The environment is not running. Run 'awx-lab up' first.")
                    username, password = lab.cluster.credentials()
                    lab.runner.write(f"URL: {state['url']}\nUsername: {username}\nPassword: {password}\n", level="result")
                    return 0
                if args.command == "down":
                    if not args.purge:
                        return lab.stop()
                    # Only clear the default pointer when purging its selected environment.
                    pointer = state_root / "current.json"
                    selected = pointer.exists() and not pointer.is_symlink() and json.loads(pointer.read_text()).get("id") == state["id"]
                    lab.purge()
                    if selected:
                        pointer.unlink()
                    return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (LabError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0
