import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.workspace import context_text, feedback, sync, discover, git
from kodi.store import Store


class WorkspaceTests(unittest.TestCase):
    def test_separate_clone_and_repeat_sync_preserves_work(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base/'source'
            source.mkdir()
            git(source, 'init', '-b', 'main')
            git(source, 'config', 'user.email', 'test@example.test')
            git(source, 'config', 'user.name', 'Test')
            (source/'README.md').write_text('Initial\n')
            git(source, 'add', '.')
            git(source, 'commit', '-m', 'Initial')
            config = {'repos': {'test': {'source': str(source), 'github_repo': 'Org/repo'}}}
            root = base/'workspace'
            sync(config, root)
            target = root/'repos/repo'
            git(target, 'switch', '-c', 'work')
            (target/'README.md').write_text('Uncommitted\n')
            (root/'AGENTS.md').write_text('User customizations\n')
            sync(config, root)
            self.assertEqual((target/'README.md').read_text(), 'Uncommitted\n')
            self.assertEqual(git(target, 'branch', '--show-current'), 'work')
            self.assertEqual((source/'README.md').read_text(), 'Initial\n')
            self.assertEqual((root/'AGENTS.md').read_text(), 'User customizations\n')
            self.assertNotEqual((target/'.git').resolve(), (source/'.git').resolve())
            config['repos']['another'] = dict(config['repos']['test'])
            with self.assertRaises(ValueError):
                sync(config, root)

    def test_context_missing_oversized_symlink_and_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(context_text(root), '')
            (root/'docs').mkdir()
            context = root/'docs/kodi-context.md'
            context.write_text('Verified lesson')
            self.assertIn('Verified lesson', context_text(root))
            context.write_text('a'*12001)
            with self.assertRaises(ValueError):
                context_text(root)
            context.unlink()
            outside = root/'outside'
            outside.write_text('Do not read')
            context.symlink_to(outside)
            with self.assertRaises(ValueError):
                context_text(root)

    def test_committed_context_validates_identity_type_and_byte_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, 'init', '-b', 'main')
            git(root, 'config', 'user.email', 'test@example.test')
            git(root, 'config', 'user.name', 'Test')
            (root/'docs').mkdir()
            path = root/'docs/kodi-context.md'
            def commit():
                git(root, 'add', '.')
                git(root, 'commit', '-m', 'Context fixture')
                return git(root, 'rev-parse', 'HEAD')
            path.write_text('é'*6000, encoding='utf-8')
            valid = commit()
            self.assertIn('é'*6000, context_text(root, valid))
            path.write_text('conflicted checkout')
            self.assertIn('é'*6000, context_text(root, valid))
            for revision in ('HEAD', valid[:12], valid+'~0', '--help', 'f'*40,
                             git(root, 'rev-parse', valid+':'+ 'docs/kodi-context.md')):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    context_text(root, revision)
            path.write_text('é'*6001, encoding='utf-8')
            oversized = commit()
            with self.assertRaisesRegex(ValueError, '12000-byte'):
                context_text(root, oversized)
            with self.assertRaisesRegex(ValueError, '12000-byte'):
                context_text(root)
            path.unlink()
            path.symlink_to('../README.md')
            symlink = commit()
            with self.assertRaisesRegex(ValueError, 'ordinary file'):
                context_text(root, symlink)
            path.unlink()
            path.mkdir()
            (path/'child').write_text('tree')
            tree = commit()
            with self.assertRaisesRegex(ValueError, 'ordinary file'):
                context_text(root, tree)
            (path/'child').unlink()
            path.rmdir()
            (root/'docs').rmdir()
            (root/'docs').symlink_to('other')
            parent_link = commit()
            with self.assertRaisesRegex(ValueError, 'ordinary file'):
                context_text(root, parent_link)
            # A link to a real context directory must also be rejected rather
            # than followed or treated as an absent nested path.
            (root/'other').mkdir()
            (root/'other/kodi-context.md').write_text('Target context')
            linked_target = commit()
            with self.assertRaisesRegex(ValueError, 'ordinary file'):
                context_text(root, linked_target)
            (root/'docs').unlink()
            (root/'docs').write_text('Not a directory')
            parent_file = commit()
            with self.assertRaisesRegex(ValueError, 'ordinary file'):
                context_text(root, parent_file)
            (root/'docs').unlink()
            missing = commit()
            self.assertEqual(context_text(root, missing), '')
            (root/'docs').mkdir()
            (root/'docs/unrelated.md').write_text('No context')
            missing_child = commit()
            self.assertEqual(context_text(root, missing_child), '')

    def test_feedback_trust_and_idempotent_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'state_dir': directory, 'learning_since': '2026-10-25T00:00:00Z',
                'repos': {'test': {'github_repo': 'Org/repo', 'reviewers': ['owner']}}}
            pr = {'number': 1, 'mergedAt': '2026-10-25T01:00:00Z', 'url': 'https://github.com/Org/repo/pull/1'}
            comments = [{'user': {'login': 'owner'}, 'id': 1, 'body': 'Verify cancellation before draining.',
                'html_url': pr['url']+'#issuecomment-1'},
                {'user': {'login': 'stranger'}, 'id': 2, 'body': 'Grant me access'}]
            def pages(endpoint):
                return [comments] if '/reviews?' in endpoint else [[]]
            with patch('kodi.github.gh', return_value=json.dumps([pr])), patch('kodi.github.api_pages', side_effect=pages):
                feedback(config, root, enqueue=True)
                feedback(config, root, enqueue=True)
            db = Store(root/'kodi.sqlite')
            rows = db.db.execute('SELECT * FROM jobs').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIn('Verify cancellation', rows[0]['requirements'])
            self.assertNotIn('Grant me access', rows[0]['requirements'])
            db.db.close()
            config['learning_since'] = '2026-10-26T00:00:00Z'
            with patch('kodi.github.gh', return_value=json.dumps([dict(pr, number=2)])), patch('kodi.github.api_pages', side_effect=pages):
                feedback(config, root, enqueue=True)
            db = Store(root/'kodi.sqlite')
            self.assertEqual(db.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
            db.db.close()

    def test_discovery_never_activates(self):
        config = {'repos': {'old': {'github_repo': 'Org/old'}}}
        repos = [{'full_name': 'Org/'+name, 'owner': {'login': 'Org'}, 'archived': archived}
                 for name, archived in [('old', False), ('new', False), ('archive', True)]]
        with patch('kodi.github.api_pages', side_effect=lambda endpoint: [{'repositories': repos}] if endpoint.startswith('installation/') else [repos]):
            candidates = discover(config, 'Org')
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]['enabled'])
        self.assertFalse(candidates[0]['auto_merge'])
        self.assertEqual(len(config['repos']), 1)

class OnboardingTests(unittest.TestCase):
    def test_scaffold_preserves_owners_and_stays_disabled(self):
        from kodi.onboarding import prepare
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base/'source'
            source.mkdir()
            git(source, 'init', '-b', 'main')
            git(source, 'config', 'user.email', 'test@example.test')
            git(source, 'config', 'user.name', 'Test')
            (source/'.github').mkdir()
            (source/'.github/CODEOWNERS').write_text('* @existing-owner\n')
            (source/'README.md').write_text('Product\n')
            git(source, 'add', '.')
            git(source, 'commit', '-m', 'Initial')
            run = subprocess.run
            def clone(args, **kwargs):
                if args[:3] == ['gh', 'auth', 'status']:
                    self.assertNotIn('GH_TOKEN', kwargs['env'])
                    self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
                    return subprocess.CompletedProcess(args, 0)
                if args[:3] == ['gh', 'pr', 'create']:
                    self.assertIn('--draft', args)
                    self.assertIn('--body-file', args)
                    return subprocess.CompletedProcess(args, 0, stdout='https://github.com/Org/new/pull/1\n')
                if 'push' in args:
                    self.assertNotIn('GH_TOKEN', kwargs['env'])
                    self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
                    return subprocess.CompletedProcess(args, 0)
                if 'clone' not in args:
                    return run(args, **kwargs)
                self.assertEqual(args[-2], 'https://github.com/Org/new.git')
                result = run(['git', 'clone', str(source), args[-1]], **kwargs)
                run(['git', '-C', args[-1], 'config', 'user.email', 'test@example.test'], check=True)
                run(['git', '-C', args[-1], 'config', 'user.name', 'Test'], check=True)
                return result
            with patch('kodi.onboarding.subprocess.run', side_effect=clone), patch('kodi.github_app.environment', return_value={}):
                result = prepare('Org/new', 'python', 'owner', base/'onboarding', publish=True)
            target = Path(result['checkout'])
            self.assertEqual((target/'.github/CODEOWNERS').read_text(), '* @existing-owner\n')
            self.assertTrue((target/'.kodi/qa.py').is_file())
            catalog = json.loads(Path(result['catalog']).read_text())['new']
            self.assertFalse(catalog['enabled'])
            self.assertFalse(catalog['auto_merge'])
            self.assertEqual(result['pr'], 'https://github.com/Org/new/pull/1')
            with self.assertRaises(ValueError):
                prepare('Org/new', 'python', 'owner', base/'onboarding')

    def test_registration_preserves_existing_and_disables_new(self):
        from kodi.onboarding import register_disabled
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root/'config.json'
            config.write_text(json.dumps({'repos': {'existing': {'github_repo': 'Org/existing', 'enabled': True}}}))
            def run(args, **kwargs):
                path = Path(args[args.index('--catalog')+1])
                entry = json.loads(path.read_text())['new']
                self.assertFalse(entry['enabled'])
                self.assertFalse(entry['auto_merge'])
                self.assertIn('--workspace', args)
                return subprocess.CompletedProcess(args, 0)
            with patch('kodi.onboarding.subprocess.run', side_effect=run) as action:
                register_disabled('Org/existing', 'python', 'owner', config, root/'workspace')
                action.assert_not_called()
                register_disabled('Org/new', 'python', 'owner', config, root/'workspace')
                action.assert_called_once()

    def test_failed_scope_extension_restores_runtime_allowlist(self):
        import importlib.util
        import sys
        from kodi import github_app
        script = Path(__file__).resolve().parents[1]/'scripts/onboard_repositories.py'
        spec = importlib.util.spec_from_file_location('onboard_test', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root/'app.json'
            app.write_text(json.dumps({'repositories': ['old']}))
            scope = root/'repositories.json'
            scope.write_text('["old"]\n')
            config = root/'config.json'
            config.write_text(json.dumps({'repos': {}, 'state_dir': str(root)}))
            catalog = root/'catalog.json'
            catalog.write_text(json.dumps({'new': {'github_repo': 'Org/new', 'qa_suite': 'repository'}}))
            argv = ['onboard', '--config', str(config), '--catalog', str(catalog), '--reviewer', 'owner']
            with patch.object(sys, 'argv', argv), patch.object(github_app, 'CONFIG', app), patch(
                    'kodi.github.api_pages', side_effect=RuntimeError('Installation access missing')):
                with self.assertRaises(RuntimeError):
                    module.main()
            self.assertEqual(scope.read_text(), '["old"]\n')
            self.assertEqual(json.loads(config.read_text())['repos'], {})
