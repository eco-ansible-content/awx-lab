import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from awx_lab.cli import main, parser
from awx_lab.cluster import Cluster
from awx_lab.lab import Lab
from awx_lab.system import CommandError, LabError, Runner
from test_reuse import make_lab


PR = "https://github.com/ansible/awx/pull/8"
CHECKS = ["API ping", "admin authentication", "credential type registration"]


class LogModeTests(unittest.TestCase):
    def scenario(self, mode, failure=None, command="test", fresh=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        lab = make_lab(Path(temporary.name))
        if fresh:
            lab.state.update(cluster_created=False, bootstrapped=False, build=None, sources={})
            lab.save()
        calls = []
        def preparation(instance):
            calls.append("prerequisites")
            with instance.step("prerequisite detail"):
                instance.runner.run([sys.executable, "-c", "print('environment setup: 0 errors')"])
                if failure == "setup":
                    instance.runner.run([sys.executable, "-c", "import sys; print('SETUP BROKE'); sys.exit(7)"])
                if failure == "interrupt":
                    raise KeyboardInterrupt()
        def build(builder, image, **kwargs):
            calls.append("build")
            builder.runner.run([sys.executable, "-c", "print('compiling application')"])
            return {"image_id": "image"}
        def refresh(cluster, image):
            calls.append("deploy")
            cluster.runner.run([sys.executable, "-c", "print('deployment commands')"])
        def smoke():
            calls.append("smoke")
            if failure == "test":
                raise LabError("Admin authentication smoke check failed.")
            return {"version": "1.0", "checks": CHECKS}
        arguments = ["--state-dir", temporary.name, command]
        if command == "test":
            arguments.append(PR)
        if mode != "clean":
            arguments.append("--" + mode + "-log")
        with contextlib.ExitStack() as stack:
            out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            err = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            stack.enter_context(patch.object(Lab, "prerequisites", preparation))
            stack.enter_context(patch.object(Lab, "prepare_sources", return_value={"awx": {"sha": "new"}}))
            stack.enter_context(patch.object(Lab, "prepare", lambda instance: instance.state.update(sources={"awx": {"sha": "baseline"}})))
            stack.enter_context(patch("awx_lab.lab.Builder.build", build))
            stack.enter_context(patch.object(Cluster, "require_existing"))
            stack.enter_context(patch.object(Cluster, "create"))
            stack.enter_context(patch.object(Cluster, "deploy"))
            stack.enter_context(patch.object(Cluster, "start"))
            stack.enter_context(patch.object(Cluster, "wait_ready"))
            stack.enter_context(patch.object(Cluster, "refresh", refresh))
            stack.enter_context(patch.object(Cluster, "diagnostics"))
            stack.enter_context(patch.object(Cluster, "credentials", return_value=("admin", "private-password")))
            api = stack.enter_context(patch("awx_lab.lab.API"))
            api.return_value.smoke.side_effect = smoke
            code = main(arguments)
        state = json.loads((lab.path / "state.json").read_text())
        log = (lab.path / "run.log").read_text()
        self.assertNotIn("log_mode", state["settings"])
        self.assertNotIn("private-password", json.dumps(state))
        self.assertNotIn("private-password", (lab.path / "report.json").read_text())
        self.assertNotIn("private-password", err.getvalue())
        if failure:
            self.assertNotIn("private-password", out.getvalue() + log)
        else:
            self.assertIn("Username: admin\nPassword: private-password", log)
            self.assertEqual("Password: private-password" in out.getvalue(), mode != "error")
            self.assertLess(log.index("Result: ready"), log.index("Password: private-password"))
        return code, out.getvalue(), err.getvalue(), log, calls, state

    def test_success_modes_change_only_terminal_output(self):
        all_calls = []
        for mode in ("clean", "error", "full"):
            with self.subTest(mode=mode):
                code, out, err, log, calls, state = self.scenario(mode)
                all_calls.append(calls)
                self.assertEqual(code, 0)
                self.assertEqual(err, "")
                self.assertEqual(state["last_test_result"], "passed")
                for detail in ("environment setup: 0 errors", "compiling application", "deployment commands"):
                    self.assertIn(detail, log)
                    self.assertEqual(detail in out, mode == "full")
                for check in CHECKS:
                    self.assertIn("PASS " + check, log)
                    self.assertEqual("PASS " + check in out, mode != "error")
                self.assertEqual("Result: ready" in out, mode != "error")
                if mode == "error":
                    self.assertEqual(out, "")
                if mode == "clean":
                    self.assertNotIn("$ ", out)
                    self.assertIn("== fetch and merge ==", out)
                    self.assertIn("Completed: build updated AWX image", out)
        self.assertTrue(all(calls == all_calls[0] for calls in all_calls))

    def test_setup_and_test_failures_are_visible_in_every_mode(self):
        for mode in ("clean", "error", "full"):
            for failure, message in (("setup", "SETUP BROKE"), ("test", "Admin authentication")):
                with self.subTest(mode=mode, failure=failure):
                    code, out, err, log, calls, state = self.scenario(mode, failure)
                    self.assertEqual(code, 1)
                    self.assertIn(message, err)
                    self.assertIn("Reports:", err)
                    self.assertIn(message, log)
                    self.assertNotIn("Result: ready", out + log)
                    self.assertEqual(state["last_test_result"], "failed")
                    if failure == "setup":
                        self.assertNotIn("smoke", calls)
                    if mode == "error":
                        self.assertEqual(out, "")

    def test_interrupt_remains_visible_and_returns_130(self):
        for mode in ("clean", "error", "full"):
            with self.subTest(mode=mode):
                code, out, err, log, _, state = self.scenario(mode, "interrupt")
                self.assertEqual(code, 130)
                self.assertIn("Interrupted during", err)
                self.assertIn("Reports:", err)
                self.assertEqual(state["status"], "interrupted")
                if mode == "error":
                    self.assertEqual(out, "")

    def test_up_uses_the_requested_mode_for_a_retained_environment(self):
        for mode in ("clean", "error", "full"):
            with self.subTest(mode=mode):
                code, out, _, log, _, _ = self.scenario(mode, command="up")
                self.assertEqual(code, 0)
                self.assertIn("== start existing environment ==", log)
                self.assertEqual("== start existing environment ==" in out, mode != "error")
                if mode == "error":
                    self.assertEqual(out, "")

    def test_initial_up_reports_setup_stages_and_login_in_each_mode(self):
        for mode in ("clean", "error", "full"):
            with self.subTest(mode=mode):
                code, out, _, log, _, _ = self.scenario(mode, command="up", fresh=True)
                self.assertEqual(code, 0)
                for stage in ("fetch baseline sources", "build baseline AWX image", "create kind cluster", "deploy AWX"):
                    self.assertIn(f"== {stage} ==", log)
                    self.assertIn(f"Completed: {stage}", log)
                    self.assertEqual(f"== {stage} ==" in out, mode != "error")

    def test_failed_retry_does_not_print_a_previous_successful_login(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            with patch.object(lab, "retain_artifacts"), patch.object(lab.cluster, "diagnostics"), \
                    contextlib.redirect_stdout(io.StringIO()):
                lab.operation("up", lambda: setattr(lab, "_login", ("admin", "previous-password")))
                before = len((lab.path / "run.log").read_text())
                def fail():
                    raise LabError("failed retry")
                with self.assertRaises(LabError):
                    lab.operation("up", fail)
            latest = (lab.path / "run.log").read_text()[before:]
            self.assertNotIn("previous-password", latest)
            self.assertIn("Credentials: awx-lab credentials", latest)

    def test_flags_are_mutually_exclusive_for_each_lifecycle_command(self):
        for command in (["up"], ["test", PR], ["down"]):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as error:
                parser().parse_args([*command, "--full-log", "--error-log"])
            self.assertEqual(error.exception.code, 2)

    def test_elapsed_updates_during_silent_work_stop_on_success_failure_and_interrupt(self):
        for mode in ("clean", "error", "full"):
            for failure in (None, LabError("build failed"), KeyboardInterrupt()):
                with self.subTest(mode=mode, failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                    lab = make_lab(Path(directory))
                    lab.runner.log_mode = mode
                    reported = threading.Event()
                    write = lab.runner.write

                    def output(value, *args, **kwargs):
                        write(value, *args, **kwargs)
                        if "Still running:" in value:
                            reported.set()

                    with contextlib.redirect_stdout(io.StringIO()) as screen, \
                            patch("awx_lab.system.PROGRESS_INTERVAL", 0.01), \
                            patch.object(lab.runner, "write", side_effect=output):
                        def action():
                            with lab.step("build AWX image"):
                                self.assertTrue(reported.wait(2), "silent work needs elapsed-time updates")
                                if failure:
                                    raise failure
                        if failure:
                            with self.assertRaises(type(failure)):
                                action()
                        else:
                            action()
                        # No timer remains to write after completion or interruption.
                        self.assertFalse(any(t.name == "awx-lab-progress" for t in threading.enumerate()))
                    log = (lab.path / "run.log").read_text()
                    self.assertIn("Still running: build AWX image (", log)
                    self.assertIn("s elapsed)", log)
                    self.assertEqual("Completed: build AWX image" in log, failure is None)
                    self.assertEqual("Still running:" in screen.getvalue(), mode != "error")

    def test_credentials_command_and_log_reader_show_the_saved_login(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            with patch.object(Cluster, "credentials", return_value=("admin", "local-password")), \
                    contextlib.redirect_stdout(io.StringIO()) as screen:
                self.assertEqual(main(["--state-dir", directory, "credentials"]), 0)
            expected = "Username: admin\nPassword: local-password\n"
            self.assertIn(expected, screen.getvalue())
            self.assertIn(expected, (lab.path / "run.log").read_text())
            with contextlib.redirect_stdout(io.StringIO()) as screen:
                self.assertEqual(main(["--state-dir", directory, "logs"]), 0)
            self.assertIn(expected, screen.getvalue())

    def test_down_and_purge_are_silent_in_error_mode(self):
        for purge in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                lab = make_lab(Path(directory))
                lab.state.update(cluster_created=False)
                lab.save()
                args = ["--state-dir", directory, "down", "--error-log"] + (["--purge"] if purge else [])
                with contextlib.redirect_stdout(io.StringIO()) as out, \
                        patch.object(Runner, "run", return_value=Mock(stdout="", returncode=0)):
                    self.assertEqual(main(args), 0)
                self.assertEqual(out.getvalue(), "")
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--state-dir", directory, "down", "--error-log"]), 0)
            self.assertEqual(out.getvalue(), "")

    def test_failure_before_environment_selection_is_visible(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["--state-dir", directory, "test", PR, "--error-log"]), 1)
            self.assertIn("No environment exists", err.getvalue())

    def test_output_mode_is_not_sticky_across_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            for mode in ("full", "error", "clean"):
                arguments = ["--state-dir", directory, "up"] + ([] if mode == "clean" else ["--" + mode + "-log"])
                def up(instance):
                    self.assertEqual(instance.runner.log_mode, mode)
                    return 0
                with patch.object(Lab, "up", up):
                    self.assertEqual(main(arguments), 0)
            self.assertNotIn("log_mode", json.loads((lab.path / "state.json").read_text())["settings"])

    def test_sensitive_failed_command_does_not_leak_to_error_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.log"
            for mode in ("clean", "error", "full"):
                runner = Runner(path, log_mode=mode)
                with patch.dict("os.environ", {"LAB_PRIVATE_VALUE": "do-not-print-me"}), \
                        contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(CommandError) as caught:
                    runner.run([sys.executable, "-c",
                                "import os,sys; print(os.environ['LAB_PRIVATE_VALUE']); sys.exit(1)"],
                               sensitive=True)
                self.assertNotIn("do-not-print-me", str(caught.exception) + out.getvalue() + path.read_text())

    def test_plugin_and_job_checks_show_pass_only_after_success(self):
        for mode in ("clean", "error", "full"):
            for failure in (None, "plugin", "job"):
                with self.subTest(mode=mode, failure=failure), tempfile.TemporaryDirectory() as directory:
                    lab = make_lab(Path(directory))
                    lab.runner.log_mode = mode
                    lab.state["build"]["plugin_wheel"] = {"sha256": "hash"}
                    with contextlib.ExitStack() as stack:
                        out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                        stack.enter_context(patch.object(lab.cluster, "credentials", return_value=("admin", "secret")))
                        plugin = stack.enter_context(patch.object(lab.cluster, "verify_plugin"))
                        api = stack.enter_context(patch("awx_lab.lab.API"))
                        api.return_value.smoke.return_value = {"checks": CHECKS}
                        api.return_value.job.return_value = {"status": "successful"}
                        if failure == "plugin":
                            plugin.side_effect = LabError("plugin mismatch")
                        if failure == "job":
                            api.return_value.job.side_effect = LabError("job failed")
                        if failure:
                            with self.assertRaises(LabError):
                                lab.run_checks("job")
                        else:
                            lab.run_checks("job")
                    log = (lab.path / "run.log").read_text()
                    self.assertEqual("PASS installed plugin wheel hash" in log, failure != "plugin")
                    self.assertEqual("PASS Ansible localhost ping job" in log, failure is None)
                    if failure == "plugin":
                        api.return_value.job.assert_not_called()
                    if mode == "error":
                        self.assertEqual(out.getvalue(), "")
