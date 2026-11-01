"""Disposable regression evidence, not independent acceptance."""
import json
import tempfile
import unittest
from pathlib import Path

from kodi.learning import policy, scheduler_state, waiting_reason
from kodi.store import Store


class LearningScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'queue.sqlite'
        self.store = Store(self.path)
        self.config = {'learning_schedule': {'authorized': True, 'max_product_claims': 2},
                       'learning_since': '2026-10-25',
                       'repos': {'example': {'enabled': True, 'github_repo': 'ExampleOrg/project'}}}
        self.requirements = 'Untrusted feedback data:\n'+json.dumps({'pr': {
            'number': 1, 'url': 'https://github.com/ExampleOrg/project/pull/1',
            'mergedAt': '2026-10-26T00:00:00Z'}})

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def lesson(self):
        return self.store.reconcile_learning('learning:example:pr:1', 'example', 'Lesson', self.requirements)

    def claim(self):
        return self.store.claim(eligible=lambda r: not waiting_reason(self.config, r), learning_bound=2)

    def test_product_priority_bounded_learning_service_and_restart(self):
        lesson = self.lesson()
        products = [self.store.enqueue('product:'+str(i), 'other', 'Delivery', 'Implement')['id'] for i in range(4)]
        for expected in products[:2]:
            job = self.claim()
            self.assertEqual(job['id'], expected)
            self.store.update(job['id'], state='done')
        # Metadata-only collection and a new process retain both age and debt.
        self.lesson()
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(scheduler_state(self.store)['product_claims'], 2)
        self.assertEqual(self.claim()['id'], lesson['id'])
        self.store.update(lesson['id'], state='done')
        self.assertEqual(self.claim()['id'], products[2])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='learning_scheduler'").fetchone()[0], 1)

    def test_fairness_never_authorizes_disabled_paused_or_stale_sources(self):
        lesson = self.lesson()
        # Build fairness debt without consuming learning.
        for i in range(3):
            job = self.store.enqueue('delivery:'+str(i), 'other', 'Delivery', 'Implement')
            claimed = self.store.claim(eligible=lambda r: not r['external_id'].startswith('learning:'), learning_bound=2)
            self.store.update(claimed['id'], state='done')
        for change in ('authorization', 'disabled', 'cutoff', 'identity', 'provenance'):
            with self.subTest(change=change):
                config = json.loads(json.dumps(self.config))
                if change == 'authorization': config['learning_schedule']['authorized'] = False
                if change == 'disabled': config['repos']['example']['enabled'] = False
                if change == 'cutoff': config['learning_since'] = '2026-10-27'
                if change == 'identity': config['repos']['example']['github_repo'] = 'ExampleOrg/other'
                if change == 'provenance': config.pop('learning_since')
                self.assertIsNotNone(waiting_reason(config, self.store.get(lesson['id'])))
                self.assertIsNone(self.store.claim(eligible=lambda r: not waiting_reason(config, r), learning_bound=2))
        for state in ('paused', 'deferred', 'cancelled'):
            self.store.update(lesson['id'], state=state)
            self.assertIsNone(self.claim())
        self.store.update(lesson['id'], state='queued')
        self.assertEqual(self.claim()['id'], lesson['id'])

    def test_invalid_bounds_fail_before_claim_and_default_is_unauthorized(self):
        lesson = self.lesson()
        config = dict(self.config)
        config.pop('learning_schedule')
        self.assertIn('authorization', waiting_reason(config, lesson))
        for value in (0, -1, True, 1001, '2'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): policy({'learning_schedule': {'max_product_claims': value}})
                with self.assertRaises(ValueError): self.store.claim(learning_bound=value)
        self.assertEqual(self.store.get(lesson['id'])['state'], 'queued')

    def test_accepted_model_fixes_only_and_stable_tracking_on_restart(self):
        from kodi.learning import model_feedback, observe_collection
        original = self.store.enqueue('issue:1', 'example', 'Fix', 'Preserve cancellation')
        source_url = 'https://github.com/ExampleOrg/project/pull/1'
        self.store.update(original['id'], pr_url=source_url, state='done', commit_sha='b'*40)
        def review(**extra):
            return dict(approved=True, criteria_met=True, simple=True, blocking_findings=[],
                        author_family='openai', reviewer_family='google', provider='gemini',
                        model='review-b', **extra)
        rejected = review()
        rejected.update(approved=False, model='review-a', blocking_findings=['Missing cancellation guard'])
        self.store.evidence(original['id'], 'a'*40, 'review', rejected)
        finding_id = self.store.findings(original['id'])[0]['id']
        disposition = dict(finding_id=finding_id, status='fixed with source/test evidence',
                           candidate_sha='b'*40, requirements_identity=self.store.requirements_identity(original['id']),
                           source_evidence='Cancellation guard exists in final source.',
                           test_evidence='Independent cancellation-path regression passed.')
        self.store.evidence(original['id'], 'b'*40, 'author', {'test_steps': ['Cancel before dispatch']})
        self.store.evidence(original['id'], 'b'*40, 'qa', {'passed': True})
        self.store.evidence(original['id'], 'b'*40, 'review', review(dispositions=[disposition]))
        results = model_feedback(self.store, 'example', source_url)
        self.assertEqual(len(results), 1)
        self.assertEqual(model_feedback(self.store, 'example', source_url, accepted_sha='c'*40), [])
        self.assertEqual(results[0]['reviewed_sha'], 'a'*40)
        self.assertEqual(results[0]['accepted_sha'], 'b'*40)
        self.assertEqual(results[0]['original_task'], 'issue:1')
        lesson = self.lesson()
        observe_collection(self.store, lesson['id'])
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(model_feedback(self.store, 'example', source_url), results)
        observe_collection(self.store, lesson['id'])
        tracked = self.store.db.execute("SELECT data FROM evidence WHERE job=? AND kind='learning_tracking'", (lesson['id'],)).fetchone()
        self.assertEqual(json.loads(tracked['data']), {'collections': 2, 'duplicates_avoided': 1})
        # A false finding is not a failure lesson, even if its dispute is resolved.
        disposition['status'] = 'source-demonstrated false positive'
        self.store.evidence(original['id'], 'b'*40, 'review', review(dispositions=[disposition]))
        self.assertEqual(model_feedback(self.store, 'example', source_url), [])
        disposition['status'] = 'fixed with source/test evidence'
        disposition['source_evidence'] = ''
        self.store.evidence(original['id'], 'b'*40, 'review', review(dispositions=[disposition]))
        self.assertEqual(model_feedback(self.store, 'example', source_url), [])

    def test_learning_edits_cannot_escape_context_or_exceed_utf8_bound(self):
        from kodi.learning import validate_context_edit
        root = Path(self.tmp.name)
        (root/'docs').mkdir()
        path = root/'docs/kodi-context.md'
        path.write_text('é'*6000, encoding='utf-8')
        validate_context_edit(root, ['docs/kodi-context.md'])
        path.write_text('é'*6001, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '12000-byte'):
            validate_context_edit(root, ['docs/kodi-context.md'])
        for paths in (['AGENTS.md'], ['docs/kodi-context.md', 'kodi/store.py'], []):
            with self.assertRaisesRegex(ValueError, 'only curated'):
                validate_context_edit(root, paths)
        path.unlink()
        path.symlink_to(root/'absent')
        with self.assertRaisesRegex(ValueError, 'ordinary file'):
            validate_context_edit(root, ['docs/kodi-context.md'])


class PromotionTests(unittest.TestCase):
    def setUp(self):
        import subprocess
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git = lambda *args: subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        (self.root/'docs').mkdir()
        (self.root/'tests').mkdir()
        (self.root/'guard.py').write_text('if cancelled: return\n')
        (self.root/'tests/test_guard.py').write_text('assert cancel_before_claim() is None\n')
        (self.root/'docs/kodi-context.md').write_text('Existing guidance\n\nOld guard rule\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture')
        self.sha = self.git('rev-parse', 'HEAD')
        self.url = 'https://github.com/ExampleOrg/project/pull/1#discussion_r2'
        self.record = {'pr': {'headRefOid': self.sha, 'url': self.url.split('#')[0]},
                       'comments': [{'id': 'model:1:f1', 'html_url': self.url, 'accepted_sha': self.sha}]}
        self.job = {'requirements': 'Untrusted feedback data:\n'+json.dumps(self.record)}
        self.item = {'source_id': 'model:1:f1', 'lesson': 'Check cancellation before claiming. '+self.url,
                     'replaces': 'Old guard rule', 'source_path': 'guard.py',
                     'source_excerpt': 'if cancelled: return', 'test_path': 'tests/test_guard.py',
                     'test_excerpt': 'assert cancel_before_claim() is None'}
        self.author = {'lesson_promotions': [self.item]}
        self.write()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        (self.root/'docs/kodi-context.md').write_text('Existing guidance\n\n'+self.item['lesson']+'\n')

    def validate(self):
        from kodi.learning import validate_promotions
        return validate_promotions(self.root, self.job, self.author)

    def test_verified_replacement_preserves_guidance_and_measures_growth(self):
        result = self.validate()
        self.assertEqual(result['promoted'], 1)
        self.assertEqual(result['skipped'], 0)
        self.assertGreater(result['context_growth'], 0)
        self.assertIn('Existing guidance', (self.root/'docs/kodi-context.md').read_text())

    def test_rejects_unsupported_claims_unknown_identity_and_stale_candidate(self):
        for key, value in [('source_excerpt', 'invented fix'), ('test_excerpt', 'fabricated test'),
                           ('source_id', 'other'), ('source_path', '../guard.py'),
                           ('replaces', 'nonexistent guidance')]:
            with self.subTest(key=key):
                original = self.item[key]
                self.item[key] = value
                with self.assertRaises((ValueError, KeyError)):
                    self.validate()
                self.item[key] = original
        self.record['comments'][0]['accepted_sha'] = 'a'*40
        self.job['requirements'] = 'Untrusted feedback data:\n'+json.dumps(self.record)
        with self.assertRaisesRegex(ValueError, 'candidate changed'):
            self.validate()

    def test_rejects_home_paths_in_promoted_guidance(self):
        original = self.item['lesson']
        for directory in ('home', 'Users', 'HOME', 'users'):
            with self.subTest(directory=directory):
                self.item['lesson'] = original+' /'+directory+'/person/project'
                self.write()
                with self.assertRaisesRegex(ValueError, 'private data'):
                    self.validate()
        self.item['lesson'] = original
        self.write()
        self.assertEqual(self.validate()['promoted'], 1)

    def test_promotion_validator_source_passes_privacy_scan(self):
        from scripts.check_public_source import scan
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(scan(root, ['kodi/learning.py']), [])

    def test_rejects_private_text_duplicates_and_unrelated_edits(self):
        self.item['lesson'] += ' password=private'
        self.write()
        with self.assertRaisesRegex(ValueError, 'private data'):
            self.validate()
        self.item['lesson'] = 'Guard before claim. '+self.url
        self.write()
        self.author['lesson_promotions'].append(dict(self.item))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.validate()
        self.author['lesson_promotions'].pop()
        (self.root/'docs/kodi-context.md').write_text(self.item['lesson'])
        with self.assertRaisesRegex(ValueError, 'exceed verified'):
            self.validate()

    def test_preapproval_pass_is_once_across_restart_and_never_a_job(self):
        from kodi.learning import preapproval_feedback
        path = self.root/'state.sqlite'
        store = Store(path)
        job = store.enqueue('issue:1', 'example', 'Fix', 'Implement guard')
        first = preapproval_feedback(store, job, 'Required repair')
        self.assertIn('During this required repair only', first)
        store.db.close()
        store = Store(path)
        self.assertEqual(preapproval_feedback(store, store.get(job['id']), 'Late feedback'), 'Late feedback')
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        self.assertEqual(store.get(job['id'])['state'], 'queued')
        store.db.close()

    def test_filtered_noop_and_metrics_are_durable_bounded_and_unauthorized_safe(self):
        from kodi.learning import filtered_outcome, metrics
        store = Store(self.root/'state.sqlite')
        config = {'learning_schedule': {'authorized': False}, 'learning_since': '2026-10-25',
                  'repos': {'example': {'github_repo': 'ExampleOrg/project'}}}
        pr = {'number': 1, 'url': self.url.split('#')[0], 'mergedAt': '2026-10-26'}
        filtered_outcome(store, config, 'example', 'ExampleOrg/project', pr, 'no substantive feedback')
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        config['learning_schedule']['authorized'] = True
        for _ in range(2):
            filtered_outcome(store, config, 'example', 'ExampleOrg/project', pr, 'no substantive feedback')
        job = store.db.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(job['state'], 'done')
        self.assertIsNone(store.claim())
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        self.assertEqual(metrics(store, job['id'])['promoted'], 0)
        metrics(store, job['id'], skipped=10**20)
        self.assertEqual(metrics(store, job['id'])['skipped'], 2147483647)
        store.db.close()
        store = Store(self.root/'state.sqlite')
        self.assertEqual(metrics(store, job['id'])['skipped'], 2147483647)
        store.db.close()

    def test_late_findings_create_only_one_new_job_without_reopening_original(self):
        from kodi.learning import reconcile_collection
        path = self.root/'state.sqlite'
        store = Store(path)
        requirements = lambda: 'Untrusted feedback data:\n'+json.dumps(self.record)
        original = reconcile_collection(store, 'learning:example:pr:1', 'example', 'Lesson', requirements(), self.record['pr']['url'])
        store.update(original['id'], state='done')
        self.record['comments'].append({'id': 'model:1:f2', 'html_url': self.url})
        late = reconcile_collection(store, 'learning:example:pr:1', 'example', 'Lesson', requirements(), self.record['pr']['url'])
        self.assertNotEqual(late['id'], original['id'])
        self.assertEqual(len(json.loads(late['requirements'].split('Untrusted feedback data:\n')[1])['comments']), 1)
        store.db.close()
        store = Store(path)
        reconcile_collection(store, 'learning:example:pr:1', 'example', 'Lesson', requirements(), self.record['pr']['url'])
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 2)
        self.assertEqual(store.get(original['id'])['state'], 'done')
        store.db.close()

    def test_one_time_promotion_rejects_already_applied_guidance(self):
        self.validate()
        self.git('add', 'docs/kodi-context.md')
        self.git('commit', '-qm', 'Apply fixture lesson')
        with self.assertRaisesRegex(ValueError, 'Superseded guidance'):
            self.validate()

    def test_tracking_hides_private_feedback_and_binds_authorization_to_digest(self):
        from kodi.learning import tracking_requirements
        from kodi.host_recovery import _create_internal_tracking
        from types import SimpleNamespace
        from unittest.mock import Mock
        store = Store(self.root/'state.sqlite')
        job = store.enqueue('learning:example:pr:1', 'example', 'Lesson', 'Private reviewer evidence')
        text = tracking_requirements(job)
        self.assertNotIn('Private reviewer evidence', text)
        self.assertIn('Private evidence digest:', text)
        board = Mock()
        board.tasks.return_value = []
        board.create.return_value = {'id': 'tracking-1', 'external_id': 'kodi-internal:'+str(job['id']),
                                     'description': text, 'workflow_step_id': 'backlog'}
        c = SimpleNamespace(store=store, board=board, config={'board': {'steps': {'Backlog': 'backlog'}}})
        self.assertEqual(_create_internal_tracking(c, job), 'tracking-1')
        self.assertEqual(board.create.call_args.args[2], text)
        board.move.assert_not_called()
        store.update(job['id'], requirements='Changed private evidence')
        self.assertNotEqual(tracking_requirements(store.get(job['id'])), text)
        store.db.close()

    def test_filtered_outcome_is_atomic_if_completion_write_fails(self):
        from kodi.learning import filtered_outcome
        store = Store(self.root/'state.sqlite')
        config = {'learning_schedule': {'authorized': True}, 'learning_since': '2026-10-25',
                  'repos': {'example': {'github_repo': 'ExampleOrg/project'}}}
        pr = {'number': 1, 'url': self.url.split('#')[0], 'mergedAt': '2026-10-26'}
        store.db.execute("CREATE TEMP TRIGGER reject_completion BEFORE UPDATE ON jobs WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        with self.assertRaises(Exception):
            filtered_outcome(store, config, 'example', 'ExampleOrg/project', pr, 'no substantive feedback')
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        self.assertIsNone(store.claim())
        store.db.close()

    def test_claim_requires_live_ready_and_unchanged_private_evidence_digest(self):
        from kodi.coordinator import Coordinator
        from kodi.learning import tracking_requirements
        from unittest.mock import Mock
        store = Store(self.root/'state.sqlite')
        record = {'pr': {'url': self.url.split('#')[0], 'number': 1, 'mergedAt': '2026-10-26'}}
        job = store.enqueue('learning:example:pr:1', 'example', 'Lesson',
                            'Untrusted feedback data:\n'+json.dumps(record), board_id='lesson-task')
        c = object.__new__(Coordinator)
        c.store = store
        c.config = {'learning_schedule': {'authorized': True}, 'learning_since': '2026-10-25',
                    'repos': {'example': {'github_repo': 'ExampleOrg/project'}},
                    'board': {'steps': {'Ready': 'ready'}}}
        c.board = Mock()
        for method in ('finish_prs', 'sync', 'finish_checks', 'request_reviews', 'maintain_merges', 'progress', 'process'):
            setattr(c, method, Mock())
        c.dependency_blockers = Mock(return_value=[])
        for state, description in [('paused', tracking_requirements(job)), ('ready', 'changed digest')]:
            c.board.tasks.return_value = [{'id': 'lesson-task', 'workflow_step_id': state, 'description': description}]
            c._tick()
            c.process.assert_not_called()
            self.assertEqual(store.get(job['id'])['state'], 'queued')
        c.board.tasks.return_value = [{'id': 'lesson-task', 'workflow_step_id': 'ready',
                                      'description': tracking_requirements(job)}]
        c._tick()
        c.process.assert_called_once()
        self.assertEqual(store.get(job['id'])['state'], 'coding')
        store.db.close()
