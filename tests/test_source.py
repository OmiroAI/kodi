import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.source import refresh_source
from kodi.coordinator import Coordinator


@unittest.skipUnless(shutil.which('git'), 'Disposable repository tests require Git on PATH')
class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.upstream = self.root / 'upstream'
        self.source = self.root / 'source'
        self.run_git(self.root, 'init', '-b', 'main', str(self.upstream))
        self.commit(self.upstream, 'initial')
        self.run_git(self.root, 'clone', str(self.upstream), str(self.source))
        self.run_git(self.source, 'remote', 'set-url', 'origin', 'git@github.com:owner/repo.git')
        self.repo = {'source': str(self.source), 'github_repo': 'owner/repo'}
        self.real_run = subprocess.run
        self.fetches = []
        self.env = patch('kodi.source.environment', return_value={'OFFLINE': 'yes'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.commands = patch('kodi.source.subprocess.run', side_effect=self.offline_run)
        self.commands.start()
        self.addCleanup(self.commands.stop)

    def run_git(self, cwd, *args):
        return subprocess.check_output(['git', *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, cwd, text):
        (cwd / 'file').write_text(text)
        self.run_git(cwd, 'add', 'file')
        self.run_git(cwd, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', text)
        return self.run_git(cwd, 'rev-parse', 'HEAD')

    def offline_run(self, command, **kwargs):
        if 'fetch' in command:
            self.fetches.append(command)
            self.assertEqual(kwargs['env'], {'OFFLINE': 'yes'})
            self.assertIn('credential.helper=!gh auth git-credential', command)
            command = [str(self.upstream) if item == 'https://github.com/owner/repo.git' else item for item in command]
            kwargs.pop('env')
        if kwargs.get('capture_output'):
            self.assertIn('core.hooksPath=/dev/null', command)
        return self.real_run(command, **kwargs)

    def test_clean_fast_forward_and_equal(self):
        expected = self.commit(self.upstream, 'upstream change')
        refresh_source(self.repo)
        self.assertEqual(self.run_git(self.source, 'rev-parse', 'HEAD'), expected)
        refresh_source(self.repo)
        self.assertEqual(len(self.fetches), 2)
        self.assertEqual(self.run_git(self.source, 'remote', 'get-url', 'origin'), 'git@github.com:owner/repo.git')

    def test_dirty_and_untracked_block_before_fetch(self):
        for path in ('file', 'untracked'):
            with self.subTest(path=path):
                original = (self.source / path).read_text() if path == 'file' else None
                (self.source / path).write_text('operator work')
                with self.assertRaisesRegex(RuntimeError, 'dirty or untracked'):
                    refresh_source(self.repo)
                self.assertEqual((self.source / path).read_text(), 'operator work')
                if original is None:
                    (self.source / path).unlink()
                else:
                    (self.source / path).write_text(original)
        self.assertFalse(self.fetches)

    def test_fetch_settings_cannot_hide_a_new_upstream_commit(self):
        refresh_source(self.repo)
        self.run_git(self.source, 'config', 'remote.origin.prune', 'true')
        expected = self.commit(self.upstream, 'fresh upstream')
        refresh_source(self.repo)
        self.assertEqual(self.run_git(self.source, 'rev-parse', 'HEAD'), expected)
        self.assertIn('--write-fetch-head', self.fetches[-1])
        self.assertIn('--no-prune', self.fetches[-1])
        self.assertIn('--refmap=', self.fetches[-1])

    def test_fetch_ref_mapping_cannot_overwrite_local_main(self):
        local = self.commit(self.source, 'local commit')
        self.commit(self.upstream, 'upstream commit')
        self.run_git(self.source, 'config', 'remote.origin.fetch', '+refs/heads/main:refs/heads/main')
        with self.assertRaisesRegex(RuntimeError, 'ahead of or diverged'):
            refresh_source(self.repo)
        self.assertEqual(self.run_git(self.source, 'rev-parse', 'HEAD'), local)

    def test_wrong_branch_and_origin(self):
        self.run_git(self.source, 'checkout', '-b', 'other')
        with self.assertRaisesRegex(RuntimeError, 'main'):
            refresh_source(self.repo)
        self.run_git(self.source, 'checkout', 'main')
        self.run_git(self.source, 'remote', 'set-url', 'origin', 'https://github.com/other/repo.git')
        with self.assertRaisesRegex(RuntimeError, 'origin'):
            refresh_source(self.repo)
        self.assertFalse(self.fetches)

    def test_ahead_and_divergence_preserve_local_commits(self):
        local = self.commit(self.source, 'local change')
        for diverged in (False, True):
            if diverged:
                self.commit(self.upstream, 'different upstream change')
            with self.assertRaisesRegex(RuntimeError, 'ahead of or diverged'):
                refresh_source(self.repo)
            self.assertEqual(self.run_git(self.source, 'rev-parse', 'HEAD'), local)
            self.assertEqual((self.source / 'file').read_text(), 'local change')

    def test_failed_fetch_does_not_update(self):
        before = self.run_git(self.source, 'rev-parse', 'HEAD')
        self.commit(self.upstream, 'new')
        def fail(command, **kwargs):
            if 'fetch' in command:
                return subprocess.CompletedProcess(command, 1, '', 'sensitive failure')
            return self.offline_run(command, **kwargs)
        with patch('kodi.source.subprocess.run', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Git operation failed') as error:
                refresh_source(self.repo)
        self.assertNotIn('sensitive', str(error.exception))
        self.assertEqual(self.run_git(self.source, 'rev-parse', 'HEAD'), before)

    def test_invalid_checkout_and_local_only(self):
        with self.assertRaisesRegex(RuntimeError, 'separate Git checkout'):
            refresh_source(dict(self.repo, source=str(self.root)))
        with patch('kodi.source.environment') as auth:
            refresh_source({'source': str(self.root)})
            auth.assert_not_called()


class SourcePrerequisiteTests(unittest.TestCase):
    def test_missing_git_blocks_without_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / '.git').mkdir()
            with patch('kodi.source.subprocess.run', side_effect=FileNotFoundError('git')), patch('kodi.source.environment') as auth:
                with self.assertRaisesRegex(RuntimeError, 'Canonical source: Git operation failed'):
                    refresh_source({'source': directory, 'github_repo': 'owner/repo'})
                auth.assert_not_called()


class RefreshIntegrationTests(unittest.TestCase):
    def test_failed_rebased_qa_gets_bounded_repair_then_new_commit_validation(self):
        self.coordinator.store.update(self.id,commit_sha='old',attempt=2)
        self.coordinator.store.evidence(self.id,'old','author',{'test_steps':['try it']})
        checkout=Path(self.temp.name)/'jobs'/str(self.id)/'checkout';checkout.mkdir(parents=True)
        candidate=['rebased']
        def git_at(p,*args):
            if args==('rev-parse','FETCH_HEAD'):return 'base'
            if args==('rev-parse','main'):return 'old-base'
            if args==('rev-parse','HEAD'):return candidate[0]
            return ''
        def repair(*args):
            candidate[0]='repaired'
            return {'test_steps':['try repaired candidate']}
        with patch('kodi.coordinator.refresh_source'),patch('kodi.coordinator.git',side_effect=git_at), \
                patch('kodi.coordinator.subprocess.run',side_effect=[Mock(returncode=0),Mock(returncode=1),Mock(returncode=0)]), \
                patch('kodi.coordinator.qa',side_effect=[{'passed':False,'failure_signal':'test_diagnostic','log':'test regression'},{'passed':True}]) as qa, \
                patch('kodi.coordinator.repair_candidate',side_effect=repair) as repairing, \
                patch.object(self.coordinator,'scheduled_review',return_value={'approved':True}) as review, \
                patch.object(self.coordinator.store,'gate',return_value=True):
            result=self.coordinator.refresh_base(self.id)
        self.assertEqual([call.args[2] for call in qa.call_args_list],['rebased','repaired'])
        self.assertEqual(review.call_args.args[2],'repaired')
        repairing.assert_called_once()
        self.assertEqual(result['commit_sha'],'repaired');self.assertEqual(result['attempt'],2)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / 'source'
        (source / '.git').mkdir(parents=True)
        self.repo = {'source': str(source), 'github_repo': 'owner/repo', 'qa_suite': 'system'}
        self.coordinator = Coordinator({'state_dir': self.temp.name, 'repos': {'system': self.repo}})
        self.addCleanup(self.coordinator.store.db.close)
        self.id = self.coordinator.store.enqueue('issue', 'system', 'Test', 'Requirements')['id']

    def test_process_refreshes_before_clone_and_blocks_on_failure(self):
        events = []
        def refresh(repo):
            self.assertEqual(repo, self.repo)
            events.append('refresh')
        def clone(*args, **kwargs):
            events.append('clone')
            raise RuntimeError('stop after clone')
        with patch('kodi.coordinator.refresh_source', side_effect=refresh), patch('kodi.coordinator.subprocess.run', side_effect=clone):
            self.coordinator.process(self.coordinator.store.get(self.id))
        self.assertEqual(events, ['refresh', 'clone'])
        with patch('kodi.coordinator.refresh_source', side_effect=RuntimeError('source blocker')), patch('kodi.coordinator.subprocess.run') as clone:
            self.coordinator.process(self.coordinator.store.get(self.id))
            clone.assert_not_called()
        self.assertIn('Inspect private evidence',self.coordinator.store.get(self.id)['error'])

    def test_publish_refreshes_before_base_fetch_and_blocks_publication(self):
        self.coordinator.store.update(self.id, commit_sha='candidate')
        events = []
        def refresh(repo):
            events.append('source')
        def git(*args):
            events.append('base')
            raise RuntimeError('stop at base fetch')
        with patch.object(self.coordinator.store, 'gate', return_value=True), patch('kodi.coordinator.refresh_source', side_effect=refresh), patch('kodi.coordinator.git', side_effect=git), patch('kodi.coordinator.github.publish') as publish:
            with self.assertRaisesRegex(RuntimeError, 'base fetch'):
                self.coordinator.publish(self.id)
            publish.assert_not_called()
        self.assertEqual(events, ['source', 'base'])

    def test_new_base_reruns_exact_commit_qa_and_review(self):
        self.coordinator.store.update(self.id, commit_sha='old')
        self.coordinator.store.evidence(self.id, 'old', 'author', {'test_steps': ['test']})
        checkout = Path(self.temp.name) / 'jobs' / str(self.id) / 'checkout'
        checkout.mkdir(parents=True)
        with patch('kodi.coordinator.refresh_source') as refresh, patch('kodi.coordinator.git', side_effect=['fresh-base', '', 'old-base', '', 'new-candidate']), patch('kodi.coordinator.subprocess.run', side_effect=[Mock(returncode=0), Mock(returncode=1), Mock(returncode=0)]), patch('kodi.coordinator.qa', return_value={'passed': True}) as qa, patch.object(self.coordinator, 'scheduled_review', return_value={'approved': True}) as review, patch.object(self.coordinator.store, 'gate', return_value=True):
            result = self.coordinator.refresh_base(self.id)
        refresh.assert_called_once_with(self.repo)
        qa.assert_called_once_with(self.coordinator.config, checkout, 'new-candidate', 'system')
        self.assertEqual(review.call_args.args[2], 'new-candidate')
        self.assertEqual(result['commit_sha'], 'new-candidate')

    def test_publication_source_failure_records_blocker_before_base_fetch(self):
        self.coordinator.store.update(self.id, commit_sha='candidate')
        with patch.object(self.coordinator.store, 'gate', return_value=True), patch('kodi.coordinator.refresh_source', side_effect=RuntimeError('Canonical source fetch failed')), patch('kodi.coordinator.git') as git, patch('kodi.coordinator.github.publish') as publish:
            with self.assertRaisesRegex(RuntimeError, 'Canonical source fetch failed'):
                self.coordinator.publish(self.id)
            git.assert_not_called()
            publish.assert_not_called()
        job = self.coordinator.store.get(self.id)
        self.assertEqual(job['state'], 'blocked')
        self.assertEqual(job['error'], 'Canonical source fetch failed')
