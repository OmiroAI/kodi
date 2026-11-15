import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.source import clone_checkout
from kodi.coordinator import Coordinator


class CheckoutCloneTests(unittest.TestCase):
    def test_coding_uses_repository_deadline_and_preserves_partial_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            (source / '.git').mkdir(parents=True)
            repo = {'source': str(source), 'enabled': True, 'clone_timeout': 600}
            coordinator = Coordinator({'state_dir': str(root / 'state'),
                                       'repos': {'project': repo}})
            self.addCleanup(coordinator.store.db.close)
            job = coordinator.store.enqueue('test:clone', 'project', 'Fix', 'Fix the issue')
            def interrupted(source, target, *, timeout):
                target.mkdir()
                (target / 'partial').write_text('retained')
                raise subprocess.TimeoutExpired('git clone', timeout)
            with patch.object(coordinator, 'refresh_source'), \
                    patch.object(coordinator, 'move'), \
                    patch.object(coordinator, 'progress'), \
                    patch.object(coordinator, 'notification'), \
                    patch('kodi.coordinator.clone_checkout', side_effect=interrupted) as clone:
                coordinator.process(job)
            self.assertEqual(clone.call_args.kwargs['timeout'], 600)
            self.assertEqual(coordinator.store.get(job['id'])['state'], 'blocked')
            self.assertEqual(coordinator.store.get(job['id'])['attempt'], 0)
            self.assertTrue((root / 'state/jobs/1/checkout/partial').is_file())

    @patch('kodi.source.subprocess.run')
    def test_default_is_isolated_and_disables_hooks(self, run):
        clone_checkout(Path('/source'), Path('/candidate'))
        run.assert_called_once_with(
            ['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--no-local',
             '--no-hardlinks', '/source', '/candidate'],
            check=True, capture_output=True, timeout=60)

    @patch('kodi.source.subprocess.run')
    def test_larger_deadline_does_not_retry_or_hide_timeouts(self, run):
        run.side_effect = subprocess.TimeoutExpired('git clone', 600)
        with self.assertRaises(subprocess.TimeoutExpired):
            clone_checkout('/source', '/candidate', timeout=600)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs['timeout'], 600)

    @patch('kodi.source.subprocess.run')
    def test_invalid_deadlines_never_start_a_clone(self, run):
        for value in (True, False, None, 0, -1, 901, 60.5, '600'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                clone_checkout('/source', '/candidate', timeout=value)
        run.assert_not_called()
