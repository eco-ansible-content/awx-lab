import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab.cli import main
from awx_lab.cluster import Cluster
from awx_lab.system import LabError
from test_reuse import make_lab


class StopStartTests(unittest.TestCase):
    def test_down_stops_without_deleting_or_removing_images(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            with patch('awx_lab.lab.Cluster') as cls, patch('awx_lab.lab.Runner') as runner, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--state-dir', directory, 'down']), 0)
            cls.return_value.stop.assert_called_once()
            cls.return_value.delete.assert_not_called()
            runner.return_value.run.assert_not_called()
            saved = json.loads((lab.path / 'state.json').read_text())
            self.assertTrue(saved['cluster_created'])
            self.assertTrue(saved['stopped'])
            self.assertEqual(saved['status'], 'stopped')
            self.assertEqual(saved['image'], lab.state['image'])
            self.assertTrue((Path(directory) / 'current.json').exists())

    def test_stop_failure_preserves_state_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            with patch('awx_lab.lab.Cluster') as cls, contextlib.redirect_stderr(io.StringIO()):
                cls.return_value.stop.side_effect = LabError('runtime unavailable')
                self.assertEqual(main(['--state-dir', directory, 'down']), 1)
            saved = json.loads((lab.path / 'state.json').read_text())
            self.assertTrue(saved['cluster_created'])
            self.assertEqual(saved['status'], 'stop_failed')
            cls.return_value.delete.assert_not_called()

    def test_stopped_environment_cannot_be_tested(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            lab.state['stopped'] = True
            with patch.object(lab, 'prepare_sources') as prepare, self.assertRaisesRegex(LabError, 'awx-lab up'):
                lab.refresh('https://github.com/ansible/awx/pull/8')
            prepare.assert_not_called()

    def test_runtime_stop_filters_nodes_by_exact_cluster_label_and_verifies_stopped(self):
        for runtime in ('podman', 'docker'):
            with self.subTest(runtime=runtime):
                runner = Mock(env={})
                runner.run.side_effect = [Mock(stdout='abc123\n'),
                    Mock(stdout=json.dumps([{'Id': 'abc123full', 'State': {'Running': True}}])),
                    Mock(), Mock(stdout=json.dumps([{'Id': 'abc123full', 'State': {'Running': False}}]))]
                cluster = Cluster(runner, Path('/unused'), {'id': 'awx-lab-mine', 'runtime': runtime})
                cluster.stop()
                commands = [call.args[0] for call in runner.run.call_args_list]
                self.assertIn('label=io.x-k8s.kind.cluster=awx-lab-mine', commands[0])
                self.assertEqual(commands[2], [runtime, 'stop', '--time', '60', 'abc123full'])
                self.assertFalse(any('rm' in command for command in commands))

    def test_start_keeps_same_node_and_refreshes_only_private_kubeconfig(self):
        for already_running in (False, True):
            with self.subTest(running=already_running), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'kubeconfig').write_text('private')
                runner = Mock(env={})
                def run(command, **kwargs):
                    if command[1] == 'ps':
                        return Mock(stdout='abc123\n')
                    if command[1] == 'inspect':
                        return Mock(stdout=json.dumps([{'Id': 'abc123full', 'State': {'Running': already_running}}]))
                    return Mock(stdout='ok', returncode=0)
                runner.run.side_effect = run
                cluster = Cluster(runner, root, {'id': 'awx-lab-mine', 'runtime': 'podman', 'settings': {'ready_timeout': 1}})
                cluster.start()
                commands = [call.args[0] for call in runner.run.call_args_list]
                starts = [command for command in commands if command[1] == 'start']
                self.assertEqual(starts, [] if already_running else [['podman', 'start', 'abc123']])
                self.assertIn(['kind', 'export', 'kubeconfig', '--name', 'awx-lab-mine', '--kubeconfig', root / 'kubeconfig'], commands)
                self.assertIn('--raw=/readyz', commands[-1])
                self.assertFalse(any('create' in command or 'delete' in command for command in commands))

    def test_start_readiness_timeout_never_recreates_or_deletes_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'kubeconfig').write_text('private')
            runner = Mock(env={})
            cluster = Cluster(runner, root, {'id': 'awx-lab-mine', 'runtime': 'podman', 'settings': {'ready_timeout': 1}})
            with patch.object(cluster, 'nodes', return_value=['abc']), patch.object(cluster, 'running', return_value=[]), \
                 patch.object(cluster, 'kubectl', return_value=Mock(returncode=1)), \
                 patch('awx_lab.cluster.time.monotonic', side_effect=[0, 0, 2]), patch('awx_lab.cluster.time.sleep'), \
                 self.assertRaisesRegex(LabError, 'data are preserved'):
                cluster.start()
            commands = [call.args[0] for call in runner.run.call_args_list]
            self.assertFalse(any('rm' in command or 'delete' in command or 'create' in command for command in commands))

    def test_missing_node_fails_stop_and_start(self):
        cluster = Cluster(Mock(env={}), Path('/unused'), {'id': 'awx-lab-mine', 'runtime': 'podman'})
        with patch.object(cluster, 'nodes', return_value=[]):
            for operation in (cluster.stop, cluster.start):
                with self.assertRaisesRegex(LabError, 'node container is missing'):
                    operation()
