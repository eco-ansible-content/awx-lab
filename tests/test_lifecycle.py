import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from awx_lab.cli import main, parser
from awx_lab.lab import Lab
from awx_lab.system import LabError


def options():
    args = vars(parser().parse_args(["test", "https://github.com/ansible/awx/pull/7", "--runtime", "podman", "--port", "8123"]))
    return {key: value for key, value in args.items() if key not in ("command", "state_dir")}


class LifecycleTests(unittest.TestCase):
    def scenario(self, *, command="test", keep=False, failure=None, cleanup_failure=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = options()
        config["keep"] = keep
        lab = Lab(Path(temp.name), config)
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch("awx_lab.lab.doctor", return_value={"ok": True, "checks": []}))
            stack.enter_context(patch.object(lab, "prepare"))
            builder = stack.enter_context(patch("awx_lab.lab.Builder"))
            builder.return_value.build.return_value = {"image_id": "sha256:abc"}
            create = stack.enter_context(patch.object(lab.cluster, "create"))
            deploy = stack.enter_context(patch.object(lab.cluster, "deploy"))
            stack.enter_context(patch.object(lab.cluster, "credentials", return_value=("admin", "not-in-report")))
            diagnostic = stack.enter_context(patch.object(lab.cluster, "diagnostics"))
            delete = stack.enter_context(patch.object(lab.cluster, "delete"))
            api = stack.enter_context(patch("awx_lab.lab.API"))
            api.return_value.smoke.return_value = {"checks": ["test"]}
            if failure == "build":
                builder.return_value.build.side_effect = LabError("build failed")
            elif failure == "create":
                create.side_effect = LabError("partial cluster")
            elif failure == "deploy":
                deploy.side_effect = LabError("operator failed")
            elif failure == "interrupt":
                deploy.side_effect = KeyboardInterrupt()
            elif failure == "smoke":
                api.return_value.smoke.side_effect = LabError("authentication failed")
            if cleanup_failure:
                delete.side_effect = LabError("runtime unavailable")
            if (failure and failure != "interrupt") or cleanup_failure:
                with self.assertRaises(LabError):
                    lab.execute(command)
            else:
                self.assertEqual(lab.execute(command), 130 if failure == "interrupt" else 0)
        report = json.loads((lab.path / "report.json").read_text())
        self.assertNotIn("not-in-report", (lab.path / "run.log").read_text())
        ET.parse(lab.path / "junit.xml")
        return lab, report, create, diagnostic, delete

    def test_test_success_cleans_cluster_keeps_reports(self):
        lab, report, _, diagnostic, delete = self.scenario()
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["cluster_created"])
        diagnostic.assert_called_once()
        delete.assert_called_once()
        self.assertTrue((lab.path / "junit.xml").exists())

    def test_up_and_keep_retain_lab(self):
        for kwargs, status in (({"command": "up"}, "ready"), ({"keep": True}, "passed")):
            _, report, _, _, delete = self.scenario(**kwargs)
            self.assertEqual(report["status"], status)
            self.assertTrue(report["cluster_created"])
            delete.assert_not_called()

    def test_build_failure_never_deletes_an_uncreated_cluster(self):
        _, report, create, diagnostic, delete = self.scenario(failure="build")
        self.assertEqual(report["status"], "failed")
        create.assert_not_called()
        diagnostic.assert_not_called()
        delete.assert_not_called()

    def test_partial_creation_deployment_and_smoke_failures_cleanup(self):
        for stage in ("create", "deploy", "smoke"):
            with self.subTest(stage=stage):
                _, report, _, _, delete = self.scenario(failure=stage)
                self.assertEqual(report["status"], "failed")
                delete.assert_called_once()

    def test_interrupt_collects_reports_and_cleans(self):
        _, report, _, diagnostic, delete = self.scenario(failure="interrupt")
        self.assertEqual(report["status"], "interrupted")
        diagnostic.assert_called_once()
        delete.assert_called_once()

    def test_cleanup_failure_does_not_report_pass(self):
        _, report, _, _, _ = self.scenario(cleanup_failure=True)
        self.assertEqual(report["status"], "cleanup_failed")
        self.assertTrue(report["cluster_created"])
        self.assertIn("runtime unavailable", report["cleanup_error"])

    def test_failed_kept_run_retains_diagnostics(self):
        _, report, _, diagnostic, delete = self.scenario(keep=True, failure="deploy")
        self.assertEqual(report["status"], "failed")
        diagnostic.assert_called_once()
        delete.assert_not_called()

    def test_plan_does_not_probe_runtime_or_create_files(self):
        with tempfile.TemporaryDirectory() as directory, patch("awx_lab.cli.choose_runtime") as runtime:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(["--state-dir", directory, "plan", "https://github.com/ansible/awx-plugins/pull/1", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["options"]["awx_ref"], "devel")
            runtime.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_invalid_companion_rejected_before_execution(self):
        with patch("awx_lab.cli.Lab") as lab, contextlib.redirect_stderr(io.StringIO()):
            code = main(["up", "https://github.com/ansible/awx-plugins/pull/1", "--plugins-pr", "https://github.com/ansible/awx-plugins/pull/2"])
            self.assertEqual(code, 1)
            lab.assert_not_called()

    def test_missing_prerequisites_write_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("awx_lab.lab.doctor", return_value={"ok": False, "checks": [{"status": "fail", "detail": "missing kind"}]}):
                lab = Lab(Path(directory), options())
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(LabError, "missing kind"):
                    lab.execute("test")
            report = json.loads((lab.path / "report.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(len(report["steps"]), 1)
