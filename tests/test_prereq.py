import contextlib
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab import prereq
from awx_lab.cli import main, parser
from awx_lab.system import LabError


def arguments(**values):
    defaults = dict(runtime="podman", dry_run=False, machine=None, cpus=6, memory=16384, disk_size=80)
    return SimpleNamespace(**{**defaults, **values})


def report(missing=(), runtime="podman", warning=False):
    names = ["Python", "OS", "architecture", "git", "kind", "kubectl", runtime, "runtime connection"]
    checks = [{"name": name, "status": "fail" if name in missing else "ok", "detail": name} for name in names]
    if warning:
        checks.append({"name": "runtime RAM", "status": "warn", "detail": "Low RAM"})
    return {"runtime": runtime, "checks": checks, "ok": not missing}


class PlanTests(unittest.TestCase):
    def machine_plan(self, *, machine=None, connected=False, machines=None, connections=None):
        machines = machines if machines is not None else [{"Name": "podman-machine-default", "Default": True,
                                                          "Running": False, "Memory": str(2 * 1024**3)}]
        connections = connections if connections is not None else [{"Name": "podman-machine-default", "Default": True, "IsMachine": True}]
        with patch("awx_lab.prereq.read_json_command", side_effect=[machines, connections]), patch.dict("os.environ", {}, clear=True):
            plan = prereq.Plan()
            prereq.plan_podman_machine(plan, arguments(machine=machine), Mock(), installed=True, connected=connected)
            return plan

    def test_macos_stopped_machine_is_resized_then_started_without_replacement(self):
        plan = self.machine_plan()
        self.assertFalse(plan.blockers)
        self.assertEqual([action.command for action in plan.actions], [
            ["podman", "machine", "set", "--memory", "16384", "podman-machine-default"],
            ["podman", "machine", "start", "podman-machine-default"],
        ])

    def test_existing_rootful_connection_is_preserved(self):
        plan = self.machine_plan(connections=[{"Name": "podman-machine-default-root", "Default": True, "IsMachine": True}])
        self.assertFalse(any("connection" in action.command for action in plan.actions))

    def test_new_vm_has_explicit_resources_and_default_connection(self):
        plan = self.machine_plan(machines=[], connections=[])
        commands = [action.command for action in plan.actions]
        self.assertEqual(commands[0], ["podman", "machine", "init", "--cpus", "6", "--memory", "16384", "--disk-size", "80", "awx-lab"])
        self.assertEqual(commands[-1], ["podman", "machine", "start", "awx-lab"])

    def test_running_vm_is_not_restarted_or_resized(self):
        plan = self.machine_plan(machines=[{"Name": "podman-machine-default", "Running": True, "Memory": str(2 * 1024**3)}])
        self.assertFalse(plan.actions)
        self.assertTrue(plan.notes)

    def test_unrelated_running_machine_blocks_start(self):
        plan = self.machine_plan(machines=[{"Name": "podman-machine-default", "Running": False}, {"Name": "other", "Running": True}])
        self.assertTrue(plan.blockers)
        self.assertFalse(plan.actions)

    def test_multiple_machines_without_default_require_selection(self):
        plan = self.machine_plan(machines=[{"Name": "one"}, {"Name": "two"}], connections=[])
        self.assertTrue(plan.blockers)
        self.assertFalse(plan.actions)

    def test_remote_connection_is_not_replaced_by_local_vm(self):
        plan = self.machine_plan(connections=[{"Name": "remote", "IsMachine": False, "Default": True}])
        self.assertTrue(plan.blockers)
        self.assertFalse(plan.actions)

    def test_macos_missing_binaries_use_native_official_downloads(self):
        with patch("awx_lab.prereq.architecture", return_value="arm64"):
            plan = prereq.make_plan(report(["kind", "kubectl"]), arguments(), system="Darwin")
        self.assertEqual([action.tool for action in plan.actions], ["kind", "kubectl"])
        self.assertIn("darwin-arm64", plan.actions[0].url)
        self.assertIn("darwin/arm64", plan.actions[1].url)
        self.assertTrue(plan.actions[0].checksum_url.endswith(".sha256sum"))

    def test_fedora_packages_use_dnf_and_no_vm(self):
        with patch("awx_lab.prereq.shutil.which", side_effect=lambda name: "/usr/bin/" + name if name in ("dnf", "sudo") else None), patch("awx_lab.prereq.os.geteuid", return_value=1000):
            plan = prereq.make_plan(report(["git", "podman", "runtime connection"]), arguments(), system="Linux")
        self.assertFalse(plan.blockers)
        self.assertEqual([action.command for action in plan.actions], [["sudo", "dnf", "install", "-y", "git", "podman"]])

    def test_debian_packages_refresh_apt_before_install(self):
        with patch("awx_lab.prereq.shutil.which", side_effect=lambda name: "/usr/bin/" + name if name in ("apt-get", "sudo") else None), patch("awx_lab.prereq.os.geteuid", return_value=1000):
            plan = prereq.make_plan(report(["podman", "runtime connection"]), arguments(), system="Linux")
        self.assertEqual([action.command for action in plan.actions], [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "podman"]])

    def test_missing_homebrew_is_reported_without_guessing_install_commands(self):
        with patch("awx_lab.prereq.shutil.which", return_value=None):
            plan = prereq.make_plan(report(["git"]), arguments(), system="Darwin")
        self.assertTrue(any("Homebrew" in item for item in plan.blockers))

    def test_cgroup_failure_is_a_manual_configuration_blocker(self):
        data = report()
        data["checks"].append({"name": "rootless cgroups", "status": "fail", "detail": "requires cgroup v2"})
        plan = prereq.make_plan(data, arguments(), system="Linux")
        self.assertTrue(any("cgroup" in item for item in plan.blockers))


class ApprovalTests(unittest.TestCase):
    def run_install(self, answers=None, *, dry_run=False, failure=False, verify_ok=True, no_actions=False, blocker=False):
        action = prereq.Action("Install missing package", command=["fake-package-manager", "install", "kind"])
        plan = prereq.Plan([] if no_actions else [action], ["unsupported host"] if blocker else [])
        first = report() if no_actions else report(["kind"])
        after = report() if verify_ok else report(["kind"])
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("awx_lab.prereq.choose_runtime", return_value="podman"))
            check = stack.enter_context(patch("awx_lab.prereq.doctor", side_effect=[first, after]))
            stack.enter_context(patch("awx_lab.prereq.make_plan", return_value=plan))
            prompt = stack.enter_context(patch("builtins.input", side_effect=answers if answers is not None else EOFError()))
            execute = stack.enter_context(patch("awx_lab.prereq.execute_command"))
            download = stack.enter_context(patch("awx_lab.prereq.install_binary"))
            if failure:
                execute.side_effect = LabError("download/package error")
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = prereq.install_prerequisites(arguments(dry_run=dry_run), Path("/tmp/runs"))
        return code, check, prompt, execute, download, output.getvalue()

    def test_approval_runs_actions_then_checks_again(self):
        code, check, prompt, execute, _, output = self.run_install(["y"])
        self.assertEqual(code, 0)
        self.assertEqual(check.call_count, 2)
        execute.assert_called_once()
        self.assertIn("[Y/n]", prompt.call_args.args[0])
        self.assertIn("Prerequisites verified", output)

    def test_enter_accepts_and_invalid_answer_reprompts(self):
        code, _, prompt, execute, _, _ = self.run_install(["maybe", ""])
        self.assertEqual(code, 0)
        self.assertEqual(prompt.call_count, 2)
        execute.assert_called_once()

    def test_no_and_eof_do_not_install_or_verify_again(self):
        for answer in (["n"], None):
            code, check, _, execute, download, _ = self.run_install(answer)
            self.assertEqual(code, 0)
            self.assertEqual(check.call_count, 1)
            execute.assert_not_called()
            download.assert_not_called()

    def test_dry_run_does_not_prompt_or_execute(self):
        code, check, prompt, execute, download, output = self.run_install(dry_run=True)
        self.assertEqual(code, 0)
        prompt.assert_not_called()
        execute.assert_not_called()
        download.assert_not_called()
        self.assertIn("Dry run", output)

    def test_failure_still_runs_verification_and_returns_failure(self):
        code, check, _, execute, _, output = self.run_install(["yes"], failure=True)
        self.assertEqual(code, 1)
        self.assertEqual(check.call_count, 2)
        self.assertIn("incomplete", output)

    def test_failed_final_doctor_never_reports_success(self):
        code, check, _, _, _, output = self.run_install(["yes"], verify_ok=False)
        self.assertEqual(code, 1)
        self.assertNotIn("Prerequisites verified", output)

    def test_nothing_missing_is_idempotent_and_does_not_prompt(self):
        code, check, prompt, execute, _, output = self.run_install(no_actions=True)
        self.assertEqual(code, 0)
        prompt.assert_not_called()
        execute.assert_not_called()
        self.assertIn("nothing to install", output)

    def test_blocker_prevents_partial_installation(self):
        code, _, prompt, execute, download, _ = self.run_install(blocker=True)
        self.assertEqual(code, 1)
        prompt.assert_not_called()
        execute.assert_not_called()
        download.assert_not_called()

    def test_cli_dispatches_prereq_install(self):
        with patch("awx_lab.prereq.install_prerequisites", return_value=0) as install:
            self.assertEqual(main(["prereq", "install", "--dry-run"]), 0)
            self.assertTrue(install.call_args.args[0].dry_run)
        self.assertEqual(parser().parse_args(["prereq", "install"]).memory, 16384)


class DownloadTests(unittest.TestCase):
    def check_download(self, *, checksum=None, existing=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            payload = b"#!/bin/sh\necho kind\n"
            expected = checksum or hashlib.sha256(payload).hexdigest()
            if existing:
                (path / "kind").write_bytes(b"existing tool")
            with patch("awx_lab.prereq.managed_bin_dir", return_value=path), patch("awx_lab.prereq.urllib.request.urlopen", side_effect=[io.BytesIO((expected + "  kind-linux-amd64\n").encode()), io.BytesIO(payload)]) as fetch:
                action = prereq.binary_action("kind", "Linux", "amd64")
                if checksum or existing:
                    with self.assertRaises(LabError):
                        prereq.install_binary(action)
                    self.assertEqual(list(path.iterdir()), [path / "kind"] if existing else [])
                    if existing:
                        self.assertEqual((path / "kind").read_bytes(), b"existing tool")
                        fetch.assert_not_called()
                else:
                    prereq.install_binary(action)
                    self.assertEqual((path / "kind").read_bytes(), payload)
                    self.assertEqual((path / "kind").stat().st_mode & 0o777, 0o755)
                    self.assertEqual(list(path.iterdir()), [path / "kind"])

    def test_download_is_verified_then_installed_executable(self):
        self.check_download()

    def test_bad_checksum_leaves_no_executable_or_partial_file(self):
        self.check_download(checksum="0" * 64)

    def test_existing_tool_is_never_overwritten(self):
        self.check_download(existing=True)
