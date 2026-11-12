import base64
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import qa_remote as qa
import test_qa_archives


class QAProfileTests(unittest.TestCase):
    def test_bounds_and_dependency_validation(self):
        for key, values in {
            'cpus': [True, 0, 9, '2'], 'pids_limit': [31, 1025],
            'tmp_mb': [15, 2049], 'timeout_seconds': [0, 601],
            'scratch_mb': [0, 16385], 'tools': ['python', ['bad;tool']],
            'dependency_paths': [['/workspace/cache'], ['/opt/../etc']],
            'checks': [False, ['']], 'unknown_option': [1],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    qa.validate_profile({key: value})
        qa.validate_profile({'cpus': 8, 'scratch_mb': 16384,
                             'tools': ['python'], 'dependency_paths': ['/opt/cache']})

    def test_acceptance_command_cannot_be_replaced(self):
        checks = qa.selected_checks('system', {'command': 'echo additional', 'checks': ['echo focused']})
        self.assertEqual(checks, [qa.COMMANDS['system'], 'echo additional', 'echo focused'])

    def test_preflight_failures_do_not_run_checks(self):
        profile = {'image': 'qa:test', 'command': 'expensive', 'tools': ['python'],
                   'dependency_paths': ['/opt/cache']}
        for mode, message in [('tool', 'missing tooling'), ('dependency', 'offline dependencies'),
                              ('image', 'offline image'), ('disk', 'scratch'), ('docker', 'docker')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                ok = subprocess.CompletedProcess([], 0, '', '')
                fail = subprocess.CompletedProcess([], 41 if mode == 'tool' else 42, '', '')
                with patch.object(qa.shutil, 'which', return_value=None if mode == 'docker' else 'docker'), \
                     patch.object(qa.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 0 if mode == 'disk' else 10**12})()), \
                     patch.object(qa.subprocess, 'run', side_effect=[fail if mode == 'image' else ok, fail]) as run:
                    with self.assertRaisesRegex(ValueError, message):
                        qa.preflight(profile, 'repository', Path(folder), 'qa-test')
                    self.assertTrue(all('expensive' not in str(call) for call in run.call_args_list))
                    if mode in ('tool', 'dependency'):
                        args = run.call_args_list[1].args[0]
                        self.assertIn('never', args)
                        self.assertIn('none', args)
                        self.assertIn('/opt/cache', args[-1])

    def test_run_records_profile_checks_and_exact_commit_on_preflight_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'incoming').mkdir()
            sha = 'a' * 40
            archive = test_qa_archives.QAArchiveTests().archive(root, [('source.py', 'file', 'pass')])
            archive.rename(root / 'incoming' / (sha + '.tar'))
            profile = {'image': 'qa:test', 'command': 'expensive', 'scratch_mb': 1}
            output = io.StringIO()
            with patch.object(qa, 'ROOT', root), \
                 patch.object(qa, 'profiles', return_value={'custom': profile}), \
                 patch('sys.argv', ['qa', base64.b64encode(json.dumps({'sha': sha, 'suite': 'custom'}).encode()).decode()]), \
                 patch.object(qa.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 10**12})()), \
                 patch.object(qa.shutil, 'which', return_value=None), \
                 patch.object(qa.subprocess, 'run') as run, contextlib.redirect_stdout(output):
                qa.main()
            evidence = json.loads(output.getvalue())
            self.assertEqual(evidence['sha'], sha)
            self.assertEqual(evidence['profile'], profile)
            self.assertEqual(evidence['checks'], ['expensive'])
            self.assertFalse(evidence['passed'])
            self.assertEqual(evidence['failure_signal'], 'preflight_failed')
            run.assert_not_called()

    def test_success_records_selection_and_applies_budgets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'incoming').mkdir()
            sha = 'b' * 40
            archive = test_qa_archives.QAArchiveTests().archive(root, [('source.py', 'file', 'pass')])
            archive.rename(root / 'incoming' / (sha + '.tar'))
            profile = {'image': 'qa:test', 'command': 'echo extra', 'cpus': 2,
                       'timeout_seconds': 120, 'tmp_mb': 512, 'pids_limit': 256}
            output = io.StringIO()
            with patch.object(qa, 'ROOT', root), \
                 patch.object(qa, 'profiles', return_value={'system': profile}), \
                 patch('sys.argv', ['qa', base64.b64encode(json.dumps({'sha': sha, 'suite': 'system'}).encode()).decode()]), \
                 patch.object(qa.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 10**12})()), \
                 patch.object(qa.shutil, 'which', return_value='docker'), \
                 patch.object(qa, 'preflight') as preflight, \
                 patch.object(qa.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'ok', '')) as run, \
                 contextlib.redirect_stdout(output):
                qa.main()
            evidence = json.loads(output.getvalue())
            self.assertTrue(evidence['passed'])
            self.assertEqual(evidence['sha'], sha)
            self.assertEqual(evidence['profile'], profile)
            self.assertEqual(evidence['checks'], [qa.COMMANDS['system'], 'echo extra'])
            preflight.assert_called_once()
            call = run.call_args_list[0]
            args = call.args[0]
            self.assertEqual(args[args.index('--cpus') + 1], '2')
            self.assertEqual(args[args.index('--pids-limit') + 1], '256')
            self.assertIn('size=512m', args[args.index('--tmpfs') + 1])
            self.assertEqual(call.kwargs['timeout'], 120)
            self.assertTrue(args[-1].startswith('set -e\n'))
            self.assertIn(qa.COMMANDS['system'], args[-1])

    def test_failed_check_stops_later_checks(self):
        result = subprocess.run(['sh', '-c', qa.checks_command(['echo first; exit 7', 'echo forbidden'])],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, 'first\n')

    def test_astro_preflight_requires_matching_lock_and_cached_modules(self):
        with patch.object(qa.shutil, 'which', return_value='docker'), \
             patch.object(qa.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 10**12})()), \
             patch.object(qa.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0),
                 subprocess.CompletedProcess([], 42)]) as run:
            with self.assertRaisesRegex(ValueError, 'offline dependencies'):
                qa.preflight({'image': 'qa:test'}, 'astro', Path('/tmp'), 'qa-test')
            command = run.call_args_list[1].args[0][-1]
            self.assertIn('test -d /opt/astro/node_modules', command)
            self.assertIn('cmp package-lock.json /opt/astro/package-lock.json', command)
