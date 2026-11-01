"""Verified canonical commits must exist locally before ancestry inspection."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator, WorkPending


class VerifiedBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root/'source'
        self.source.mkdir()
        self.checkout = root/'jobs/1/checkout'
        self.checkout.parent.mkdir(parents=True)
        self.git(self.source, 'init', '-b', 'main')
        (self.source/'baseline').write_text('baseline')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-m', 'baseline')
        self.git(root, 'clone', '--no-local', str(self.source), str(self.checkout))
        self.git(self.checkout, 'checkout', '-b', 'task')
        self.sha = self.git(self.checkout, 'rev-parse', 'HEAD')
        (self.source/'advance').write_text('new main')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-m', 'advance')
        self.base = self.git(self.source, 'rev-parse', 'HEAD')
        self.c = Coordinator.__new__(Coordinator)
        self.c.config = {'state_dir': str(root), 'repos': {'system': {'source': str(self.source)}}}
        self.c.store = Mock()
        self.job = {'id': 1, 'repo': 'system', 'commit_sha': self.sha}
        self.c.store.get.return_value = self.job
        self.c.refresh_source = Mock()
        self.c.dispatch = Mock()
        self.c._control_cycle = True

    def git(self, path, *args):
        return subprocess.check_output(['git', '-c', 'user.name=Test', '-c',
            'user.email=test@example.invalid', *args], cwd=path, text=True,
            stderr=subprocess.DEVNULL).strip()

    def test_missing_advanced_base_dispatches_normal_revalidation(self):
        absent = subprocess.run(['git', 'cat-file', '-e', self.base], cwd=self.checkout,
                                capture_output=True)
        self.assertNotEqual(absent.returncode, 0)
        with self.assertRaises(WorkPending):
            self.c.refresh_base(1)
        self.c.dispatch.assert_called_once_with(self.job, 'base')
        self.assertEqual(self.git(self.checkout, 'rev-parse', self.base), self.base)
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), self.sha)
        self.assertEqual(self.git(self.checkout, 'status', '--porcelain'), '')
        self.c.store.update.assert_not_called()

    def test_fetch_failure_is_actionable_and_does_not_dispatch(self):
        with patch('kodi.coordinator.git', side_effect=[self.base, RuntimeError('fetch failed')]):
            with self.assertRaisesRegex(RuntimeError, 'fetch verified main.*inspect canonical source'):
                self.c.refresh_base(1)
        self.c.dispatch.assert_not_called()

    def test_ancestry_failure_is_actionable_and_does_not_dispatch(self):
        self.job['commit_sha'] = 'missing-candidate'
        with self.assertRaisesRegex(RuntimeError, 'ancestry; inspect task checkout objects'):
            self.c.refresh_base(1)
        self.c.dispatch.assert_not_called()
