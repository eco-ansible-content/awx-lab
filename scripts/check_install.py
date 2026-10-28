"""Check a built wheel in a fresh environment, outside the source checkout.

Usage: python scripts/check_install.py dist/awx_lab-0.5.0-py3-none-any.whl
Only the supplied wheel is installed; this check needs no network or containers.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def run(command, cwd, env):
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode:
        raise RuntimeError(f"Command failed: {command}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def check(wheel: Path):
    wheel = wheel.resolve(strict=True)
    with zipfile.ZipFile(wheel) as archive:
        if "awx_lab/assets/Containerfile" not in archive.namelist():
            raise RuntimeError("The wheel is missing the build helper Containerfile.")
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="awx-lab-install-") as directory:
        root = Path(directory)
        environment = root / "isolated environment"
        unrelated = root / "unrelated directory"
        unrelated.mkdir()
        run([sys.executable, "-m", "venv", environment], unrelated, env)
        python = environment / "bin/python"
        pip = environment / "bin/pip"
        run([pip, "--disable-pip-version-check", "install", "--no-index", "--no-deps", wheel], unrelated, env)
        env["PATH"] = str(environment / "bin") + os.pathsep + env.get("PATH", "")
        env["AWX_LAB_HOME"] = str(root / "saved runs")
        if shutil.which("awx-lab", path=env["PATH"]) != str(environment / "bin/awx-lab"):
            raise RuntimeError("The installed console entry point is missing from PATH.")

        metadata = json.loads(run([python, "-I", "-c", """
import importlib.metadata
import json
from pathlib import Path
import sys
import awx_lab
from awx_lab.build import Builder
from awx_lab.local_source import LocalSource
from awx_lab.system import Runner

module = Path(awx_lab.__file__).resolve()
assert module.is_relative_to(Path(sys.prefix).resolve()), module
version = importlib.metadata.version('awx-lab')
assert version == awx_lab.__version__
builder = Builder(Runner(), 'podman', Path.cwd(), 'arm64')
assert (builder.assets / 'Containerfile').read_text().startswith('FROM ')
print(json.dumps({'version': version, 'module': str(module)}))
"""], unrelated, env))
        if run(["awx-lab", "--version"], unrelated, env) != metadata["version"]:
            raise RuntimeError("Installed command and package versions differ.")
        help_text = run(["awx-lab", "--help"], unrelated, env)
        for command in ("doctor", "prereq", "plan", "up", "test", "status", "logs", "credentials", "down"):
            if command not in help_text:
                raise RuntimeError(f"Installed CLI is missing command {command}.")
        install_help = run(["awx-lab", "prereq", "install", "--help"], unrelated, env)
        if "--dry-run" not in install_help:
            raise RuntimeError("The installed prerequisite installer is missing its preview option.")
        test_help = run(["awx-lab", "test", "--help"], unrelated, env)
        if "--reuse" in test_help or "--suite" not in test_help or "--local" not in test_help:
            raise RuntimeError("The installed CLI does not expose the single-environment test workflow.")
        invalid_local = subprocess.run(["awx-lab", "test", "--local"], cwd=unrelated, env=env,
                                       capture_output=True, text=True, timeout=30)
        if invalid_local.returncode != 1 or "Git working directory" not in invalid_local.stderr:
            raise RuntimeError("Installed local mode did not validate the caller's Git working directory.")
        for command in ("up", "test", "down"):
            help_text = run(["awx-lab", command, "--help"], unrelated, env)
            if "--error-log" not in help_text or "--full-log" not in help_text:
                raise RuntimeError(f"Installed {command} command is missing its logging modes.")
        baseline = json.loads(run(["awx-lab", "plan", "--json"], unrelated, env))
        if baseline["options"]["pr"] is not None:
            raise RuntimeError("Baseline setup unexpectedly requires a PR.")
        plan = json.loads(run(["awx-lab", "plan", "https://github.com/ansible/awx/pull/1", "--json"], unrelated, env))
        if plan["options"]["pr"] != "https://github.com/ansible/awx/pull/1":
            raise RuntimeError("Installed plan command did not preserve its arguments.")

        # Saved runs are independent of the calling directory and the installed package.
        run_id = "awx-lab-packaging-check"
        saved = Path(env["AWX_LAB_HOME"]) / run_id
        saved.mkdir(parents=True)
        state = {"id": run_id, "runtime": "podman", "status": "stopped",
                 "cluster_created": False, "url": "http://127.0.0.1:8123"}
        (saved / "state.json").write_text(json.dumps(state))
        (saved.parent / "current.json").write_text(json.dumps({"id": run_id}))
        second_directory = root / "another directory"
        second_directory.mkdir()
        for cwd in (unrelated, second_directory):
            actual = json.loads(run(["awx-lab", "status", "--json"], cwd, env))
            if actual != [state]:
                raise RuntimeError("Saved run discovery depends on the current directory.")
        run([pip, "--disable-pip-version-check", "uninstall", "--yes", "awx-lab"], unrelated, env)
        if (environment / "bin/awx-lab").exists():
            raise RuntimeError("Uninstall left the console entry point behind.")
        if not (saved / "state.json").exists():
            raise RuntimeError("Uninstall unexpectedly removed saved runs.")
    print(f"Installed awx-lab {metadata['version']} verified: PATH command, packaged assets, shared run state and clean uninstall.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    check(args.wheel)
