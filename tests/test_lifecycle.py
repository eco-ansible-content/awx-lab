import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from awx_lab.cli import main, parser, current_path
from awx_lab.lab import Lab
from awx_lab.system import LabError, atomic_json, lock


def options():
    args = vars(parser().parse_args(['up', '--runtime', 'podman', '--port', '8123']))
    return {key: value for key, value in args.items() if key not in ('command', 'state_dir')}


class LifecycleTests(unittest.TestCase):
    def scenario(self, failure=None, resume=False, pending=False, partial=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        lab = Lab(Path(temp.name), options())
        if resume:
            lab.state.update(cluster_created=True, bootstrapped=True, stopped=True,
                             build={'image_id': 'sha256:old'}, sources={'awx': {'sha': 'base'}}, status='stopped')
        if pending:
            lab.state['pending_update'] = {'image': lab.state['image'] + '-next', 'sources': {'awx': {'sha': 'next'}},
                                          'build': {'image_id': 'sha256:next'}, 'selection': {'pr': 'next'}}
        if partial:
            lab.state['bootstrapped'] = False
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch('awx_lab.lab.doctor', return_value={'ok': True, 'checks': []}))
            prepare = stack.enter_context(patch.object(lab, 'prepare'))
            builder = stack.enter_context(patch('awx_lab.lab.Builder'))
            builder.return_value.build.return_value = {'image_id': 'sha256:abc'}
            methods = {name: stack.enter_context(patch.object(lab.cluster, name))
                       for name in ('create', 'deploy', 'start', 'refresh', 'wait_ready', 'diagnostics', 'delete', 'load_image')}
            checks = stack.enter_context(patch.object(lab, 'run_checks'))
            if failure == 'build':
                builder.return_value.build.side_effect = LabError('build failed')
            elif failure == 'smoke':
                checks.side_effect = LabError('smoke failed')
            elif failure == 'interrupt':
                methods['deploy'].side_effect = KeyboardInterrupt()
            elif failure:
                methods[failure].side_effect = LabError(failure + ' failed')
            if failure and failure != 'interrupt':
                with self.assertRaises(LabError):
                    lab.up()
            else:
                self.assertEqual(lab.up(), 130 if failure == 'interrupt' else 0)
        report = json.loads((lab.path / 'report.json').read_text())
        ET.parse(lab.path / 'junit.xml')
        methods['delete'].assert_not_called()
        return lab, report, methods, builder, prepare, checks

    def test_initial_up_builds_baseline_and_retains_environment(self):
        _, report, methods, builder, _, checks = self.scenario()
        self.assertEqual(report['status'], 'ready')
        self.assertTrue(report['bootstrapped'])
        self.assertTrue(report['cluster_created'])
        self.assertNotIn('pr', report['settings'])
        methods['create'].assert_called_once()
        methods['deploy'].assert_called_once()
        builder.return_value.build.assert_called_once()
        checks.assert_called_once_with('smoke')

    def test_resume_preserves_image_and_does_not_fetch_build_or_redeploy(self):
        _, report, methods, builder, prepare, checks = self.scenario(resume=True)
        self.assertFalse(report['stopped'])
        self.assertEqual(report['build']['image_id'], 'sha256:old')
        methods['start'].assert_called_once()
        methods['wait_ready'].assert_called_once_with(report['image'])
        methods['create'].assert_not_called()
        methods['deploy'].assert_not_called()
        prepare.assert_not_called()
        builder.assert_not_called()
        checks.assert_called_once()

    def test_up_finishes_pending_update_before_reporting_ready(self):
        _, report, methods, builder, _, _ = self.scenario(resume=True, pending=True)
        methods['refresh'].assert_called_once_with(report['image'])
        self.assertEqual(report['build']['image_id'], 'sha256:next')
        self.assertNotIn('pending_update', report)
        builder.assert_not_called()

    def test_failures_and_interrupts_keep_cluster_and_report_failed_stage(self):
        for stage in ('build', 'create', 'deploy', 'smoke', 'interrupt'):
            with self.subTest(stage=stage):
                _, report, methods, _, _, _ = self.scenario(failure=stage)
                self.assertEqual(report['status'], 'interrupted' if stage == 'interrupt' else 'failed')
                self.assertEqual(report['cluster_created'], stage != 'build')
                if stage == 'build':
                    methods['create'].assert_not_called()
                    methods['diagnostics'].assert_not_called()
                else:
                    methods['diagnostics'].assert_called_once()

    def test_failed_resume_is_not_reported_as_ready(self):
        _, report, _, builder, _, checks = self.scenario(resume=True, failure='start')
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['cluster_created'])
        checks.assert_not_called()
        builder.assert_not_called()

    def test_up_retries_image_loading_after_incomplete_initial_deployment(self):
        _, report, methods, builder, _, _ = self.scenario(resume=True, partial=True)
        methods['load_image'].assert_called_once_with(report['image'])
        methods['deploy'].assert_called_once()
        methods['create'].assert_not_called()
        builder.assert_not_called()
        self.assertTrue(report['bootstrapped'])

    def test_baseline_preparation_uses_ref_without_github_pr_request(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = Lab(Path(directory), options())
            with patch('awx_lab.lab.checkout_ref', return_value={'sha': 'base'}) as ref, patch('awx_lab.lab.merge_pr') as pr:
                sources = lab.prepare_sources()
            pr.assert_not_called()
            self.assertEqual(ref.call_args.args[2], 'devel')
            self.assertEqual(sources, {'awx': {'sha': 'base'}})

    def test_plan_is_read_only_and_accepts_baseline_or_pr(self):
        for pr in ([], ['https://github.com/ansible/awx-plugins/pull/1']):
            with tempfile.TemporaryDirectory() as directory, patch('awx_lab.cli.choose_runtime') as runtime:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    code = main(['--state-dir', directory, 'plan', *pr, '--json'])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output.getvalue())['options']['awx_ref'], 'devel')
                self.assertEqual(json.loads(output.getvalue())['options']['operator_version'], 'devel')
                runtime.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_operator_selection_accepts_devel_and_release_tags(self):
        for version in ('devel', '2.19.1', '../devel', 'latest', 'devel;echo bad'):
            with self.subTest(version=version), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = main(['plan', '--operator-version', version, '--json'])
                self.assertEqual(result, 0 if version in ('devel', '2.19.1') else 1)

    def test_invalid_companion_rejected_before_execution(self):
        with patch('awx_lab.cli.Lab') as lab, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['test', 'https://github.com/ansible/awx-plugins/pull/1',
                                   '--plugins-pr', 'https://github.com/ansible/awx-plugins/pull/2']), 1)
            lab.assert_not_called()

    def test_low_memory_stops_before_fetch_and_build(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = Lab(Path(directory), options())
            data = {'ok': False, 'checks': [{'status': 'fail', 'detail': 'allocate at least 8 GiB'}]}
            with patch('awx_lab.lab.doctor', return_value=data), patch.object(lab, 'prepare') as prepare, \
                 patch('awx_lab.lab.Builder') as builder, contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(LabError, 'allocate at least 8 GiB'):
                lab.up()
            prepare.assert_not_called()
            builder.assert_not_called()
            self.assertEqual(lab.state['status'], 'failed')
            self.assertEqual(len(lab.state['steps']), 1)

    def test_resource_warnings_are_printed_and_saved_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = Lab(Path(directory), options())
            data = {'ok': True, 'checks': [{'name': 'runtime RAM', 'status': 'warn', 'detail': '12–16 GiB recommended'}]}
            with patch('awx_lab.lab.doctor', return_value=data), \
                 patch.object(lab, 'prepare', side_effect=LabError('stop after prerequisites')), \
                 contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(LabError):
                lab.up()
            self.assertIn('WARN runtime RAM', output.getvalue())
            self.assertLess(output.getvalue().index('WARN runtime RAM'), output.getvalue().index('== fetch baseline sources =='))
            log = (lab.path / 'run.log').read_text()
            self.assertLess(log.index('WARN runtime RAM'), log.index('== fetch baseline sources =='))


class DefaultEnvironmentTests(unittest.TestCase):
    def test_up_selects_one_environment_and_finds_it_from_other_directories(self):
        with tempfile.TemporaryDirectory() as directory, patch('awx_lab.cli.choose_runtime', return_value='podman'), \
             patch('awx_lab.lab.Lab.up', return_value=0) as up, contextlib.redirect_stdout(io.StringIO()):
            command = ['--state-dir', directory, 'up']
            self.assertEqual(main(command), 0)
            first = current_path(Path(directory))
            self.assertEqual(main(command), 0)
            self.assertEqual(current_path(Path(directory)), first)
            self.assertEqual(len(list(Path(directory).glob('awx-lab-*'))), 1)
            self.assertEqual(up.call_count, 2)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*command, '--headless']), 1)

    def test_busy_environment_prevents_another_operation(self):
        with tempfile.TemporaryDirectory() as directory, patch('awx_lab.lab.Lab.up', return_value=0), \
             patch('awx_lab.cli.choose_runtime', return_value='podman'), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--state-dir', directory, 'up']), 0)
            with lock(Path(directory)):
                self.assertEqual(main(['--state-dir', directory, 'down']), 1)
            with lock(current_path(Path(directory))):
                self.assertEqual(main(['--state-dir', directory, 'up']), 1)

    def test_test_requires_existing_environment_and_down_absent_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--state-dir', directory, 'test', 'https://github.com/ansible/awx/pull/1']), 1)
            self.assertEqual(main(['--state-dir', directory, 'down']), 0)
            self.assertEqual(main(['--state-dir', directory, 'down', '--purge']), 0)

    def test_selector_rejects_traversal_and_recovers_completed_purge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root / 'current.json', {'id': '../other'})
            with self.assertRaises(LabError):
                current_path(root)
            atomic_json(root / 'current.json', {'id': 'awx-lab-removed'})
            self.assertIsNone(current_path(root))
