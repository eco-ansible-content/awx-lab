import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from awx_lab.system import CommandError, LabError, doctor, runtime_info


class RuntimeReadinessTests(unittest.TestCase):
    def probe(self, failures=1, *, running=True, starting=False, rootful=False, override=None,
              remote=False, system='Darwin', runtime='podman', error='ssh: handshake failed: read: connection reset by peer'):
        info_calls = []
        connection = 'podman-machine-default' + ('-root' if rootful else '')
        def run(command, **kwargs):
            if command[1] == 'info':
                info_calls.append(command)
                if len(info_calls) <= failures:
                    raise CommandError(command, 125, error)
                return Mock(stdout=json.dumps({'host': {'arch': 'arm64', 'memTotal': 16 * 1024**3}}))
            if command[1:4] == ['system', 'connection', 'list']:
                return Mock(stdout=json.dumps([{'Name': connection, 'Default': True, 'IsMachine': not remote}]))
            if command[1:3] == ['machine', 'list']:
                return Mock(stdout=json.dumps([{'Name': 'podman-machine-default', 'Running': running, 'Starting': starting}]))
            self.fail(f'Unexpected mutation or diagnostic: {command}')
        runner = Mock()
        runner.run.side_effect = run
        with patch('awx_lab.system.platform.system', return_value=system), \
             patch('awx_lab.system.time.sleep') as sleep, patch.dict(os.environ, override or {}, clear=True):
            try:
                _, attempts = runtime_info(runner, runtime)
                failure = None
            except LabError as caught:
                attempts, failure = len(info_calls), str(caught)
        return attempts, failure, runner, sleep

    def test_running_or_starting_vm_can_recover_from_transient_ssh_error(self):
        for options in ({}, {'running': False, 'starting': True}, {'rootful': True}):
            with self.subTest(options=options):
                attempts, failure, _, sleep = self.probe(**options)
                self.assertEqual(attempts, 2)
                self.assertIsNone(failure)
                sleep.assert_called_once_with(2)

    def test_healthy_connection_does_not_query_or_wait_for_machine(self):
        attempts, failure, runner, sleep = self.probe(failures=0)
        self.assertEqual(attempts, 1)
        self.assertIsNone(failure)
        runner.run.assert_called_once()
        sleep.assert_not_called()

    def test_persistent_error_reports_running_vm_without_telling_user_to_start_it(self):
        attempts, failure, _, _ = self.probe(failures=100)
        self.assertEqual(attempts, 5)
        self.assertIn('as running, but its API is unavailable', failure)
        self.assertIn('podman --connection podman-machine-default info', failure)
        self.assertIn('podman machine ssh podman-machine-default true', failure)
        self.assertNotIn('machine start', failure)

    def test_stopped_vm_gets_named_start_advice_without_retry(self):
        attempts, failure, _, sleep = self.probe(running=False)
        self.assertEqual(attempts, 1)
        self.assertIn('is stopped', failure)
        self.assertIn('podman machine start podman-machine-default', failure)
        sleep.assert_not_called()

    def test_permissions_and_authentication_errors_do_not_retry(self):
        attempts, failure, _, sleep = self.probe(error='ssh: handshake failed: unable to authenticate, permission denied')
        self.assertEqual(attempts, 1)
        self.assertIn('unable to authenticate', failure)
        sleep.assert_not_called()

    def test_connection_overrides_and_remote_endpoints_do_not_infer_vm_state(self):
        for options in ({'override': {'CONTAINER_HOST': 'ssh://example'}},
                        {'override': {'CONTAINER_CONNECTION': 'selected'}}, {'remote': True}):
            with self.subTest(options=options):
                attempts, failure, runner, sleep = self.probe(**options)
                self.assertEqual(attempts, 1)
                self.assertNotIn('machine start', failure)
                self.assertFalse(any(call.args[0][1:3] == ['machine', 'list'] for call in runner.run.call_args_list))
                sleep.assert_not_called()

    def test_other_platforms_and_runtimes_do_not_use_mac_podman_retries(self):
        for options in ({'system': 'Linux'}, {'runtime': 'docker'}):
            with self.subTest(options=options):
                attempts, failure, runner, sleep = self.probe(**options)
                self.assertEqual(attempts, 1)
                self.assertIn('connection settings', failure)
                runner.run.assert_called_once()
                sleep.assert_not_called()

    def test_deadline_stops_retries_after_slow_probe(self):
        runner = Mock()
        runner.run.side_effect = CommandError(['podman', 'info'], 125, 'connection reset by peer')
        with patch('awx_lab.system.platform.system', return_value='Darwin'), \
             patch('awx_lab.system.podman_connection_status', return_value=(True, 'VM running; API unavailable')), \
             patch('awx_lab.system.time.monotonic', side_effect=[0, 0, 29]), \
             patch('awx_lab.system.time.sleep') as sleep, self.assertRaises(LabError):
            runtime_info(runner, 'podman')
        runner.run.assert_called_once()
        sleep.assert_not_called()

    def test_failed_diagnostics_preserve_original_error(self):
        runner = Mock()
        runner.run.side_effect = [CommandError(['podman', 'info'], 125, 'connection reset by peer'),
                                  LabError('cannot inspect connection')]
        with patch('awx_lab.system.platform.system', return_value='Darwin'), patch.dict(os.environ, {}, clear=True), \
             self.assertRaisesRegex(LabError, 'connection reset by peer') as error:
            runtime_info(runner, 'podman')
        self.assertIn('podman machine list', str(error.exception))
        self.assertNotIn('machine start', str(error.exception))

    def test_doctor_records_recovery_and_keeps_json_output_clean(self):
        value = Mock(stdout=json.dumps({'host': {'arch': 'arm64', 'memTotal': 16 * 1024**3}}))
        with patch('awx_lab.system.runtime_info', return_value=(value, 2)), \
             patch('awx_lab.system.shutil.which', return_value='/tools/tool'), \
             patch('awx_lab.system.shutil.disk_usage', return_value=Mock(free=100 * 1024**3)), \
             patch('awx_lab.system.architecture', return_value='arm64'):
            result = doctor('podman', Path('/tmp'))
        self.assertTrue(result['ok'])
        check = next(item for item in result['checks'] if item['name'] == 'runtime connection')
        self.assertIn('after 2 attempts', check['detail'])
        self.assertEqual(json.loads(json.dumps(result)), result)
