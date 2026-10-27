import tempfile
import unittest
from unittest.mock import patch

from kodi.readiness import blockers
from kodi.store import Store
import test_sync


class DependencySyncTests(unittest.TestCase):
    setUp = test_sync.SyncTests.setUp
    def test_resolution_reuses_original_ready_task(self):
        repo = self.c.config['repos']['project']
        repo['onboarding_dependencies'] = ['onboard-1']
        repo['readiness'] = [{'id': 'onboard-1', 'repo': 'project', 'checks': [
            {'id': 'qa', 'state': 'blocked', 'actor': 'operator', 'reason': 'QA unknown',
             'action': 'validate offline QA', 'evidence': 'example:qa'}]}]
        with patch('kodi.github.authenticated', return_value=True), patch(
                'kodi.github.issues', return_value=[]), patch(
                'kodi.github.create_issue', return_value='issue-1') as create:
            self.c.sync()
            create.assert_not_called()
            self.assertIn('onboard-1/qa', self.c.board.description.call_args.args[1])
            repo['readiness'][0]['checks'][0]['state'] = 'resolved'
            repo['enabled'] = False
            self.c.sync()
            create.assert_not_called()
            repo['enabled'] = True
            self.c.sync()
            self.c.sync()
            create.assert_called_once()
        self.assertEqual(self.c.store.db.execute('select board_id from jobs').fetchone()[0], 'board-1')

    def test_claim_rechecks_catalog_without_resetting_history(self):
        job = self.c.store.enqueue('issue-1', 'project', 'Feature', 'Dependency: missing')
        self.c.store.update(job['id'], attempt=2, commit_sha='a'*40, pr_url='example:pr')
        self.assertIsNone(self.c.store.claim(eligible=lambda j: not self.c.dependency_blockers(j)))
        current = self.c.store.get(job['id'])
        self.assertEqual(current['attempt'], 2)
        self.assertEqual(current['commit_sha'], 'a'*40)
        self.assertEqual(current['pr_url'], 'example:pr')


    def test_restart_resolution_preserves_job_and_evidence(self):
        job = self.c.store.enqueue('issue-1', 'project', 'Feature', 'Dependency: qa', 'board-1')
        self.c.store.update(job['id'], attempt=2, commit_sha='a'*40, pr_url='example:pr')
        self.c.store.evidence(job['id'], 'a'*40, 'author', {'test_steps': ['manual']})
        self.c.store.reserve_recovery(job['id'], 'review', 'a'*40, 2)
        self.c.store.db.close()
        self.c.store = Store(self.directory.name + '/kodi.sqlite')
        self.addCleanup(self.c.store.db.close)
        self.c.config['repos']['project']['readiness'] = [dict(id='qa', repo='project', checks=[
            dict(id='offline', state='resolved', actor='operator', reason='QA validated',
                 action='retain validation', evidence='example:qa')])]
        claimed = self.c.store.claim(eligible=lambda j: not self.c.dependency_blockers(j))
        self.assertEqual(claimed['id'], job['id'])
        self.assertEqual(claimed['attempt'], 2)
        self.assertEqual(claimed['pr_url'], 'example:pr')
        self.assertEqual(self.c.store.db.execute('select count(*) from evidence').fetchone()[0], 1)
        self.assertEqual(self.c.store.reserve_recovery(job['id'], 'review', 'a'*40, 2), 2)

    def test_backlog_is_not_dispatched_after_resolution(self):
        self.task['workflow_step_id'] = 'backlog'
        with patch('kodi.github.authenticated', return_value=False), patch('kodi.github.create_issue') as create:
            self.c.sync()
        create.assert_not_called()
        self.assertEqual(self.c.store.db.execute('select count(*) from jobs').fetchone()[0], 0)


class ReadinessTests(unittest.TestCase):
    def test_missing_duplicate_and_unknown_evidence_fail_closed(self):
        record = {'id': 'qa', 'repo': 'project', 'checks': []}
        for records in ([], [record], [record, record]):
            self.assertTrue(blockers('project', {'readiness': records}, 'Dependency: qa'))

    def test_failure_identity_restart_changed_evidence_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/state.sqlite'
            store = Store(path)
            job = store.enqueue('board:1', 'project', 'Feature', 'Original acceptance')
            other = store.enqueue('board:2', 'project', 'Feature', 'Original acceptance')
            data = dict(task='upstream-1', check='unit', baseline_sha='a'*40,
                        candidate_sha='b'*40, conditions='same offline image',
                        evidence=['example:baseline', 'example:candidate'],
                        reason='unchanged failing source', actor='operator', confirmed_unrelated=True)
            with self.assertRaises(ValueError):
                store.link_failure(job['id'], 'failure-1', dict(data, confirmed_unrelated=False))
            store.link_failure(job['id'], 'failure-1', data)
            store.link_failure(job['id'], 'failure-1', data)
            self.assertEqual(store.db.execute("select count(*) from evidence").fetchone()[0], 1)
            store.db.close()
            store = Store(path)
            try:
                store.link_failure(other['id'], 'failure-1', dict(data, evidence=['example:new']))
                self.assertEqual(len(store.linked_failures(job['id'])), 1)
                self.assertEqual(store.linked_failures(job['id'])[0]['features'], [job['id'], other['id']])
                with self.assertRaises(ValueError):
                    store.link_failure(job['id'], 'failure-1', dict(data, task='replacement'))
                sha = 'b'*40
                store.evidence(job['id'], sha, 'author', {'test_steps': ['exercise feature']})
                store.evidence(job['id'], sha, 'qa', {'passed': True})
                store.evidence(job['id'], sha, 'review', dict(approved=True, criteria_met=True,
                    simple=True, author_family='openai', reviewer_family='google'))
                self.assertFalse(store.gate(job['id'], sha))
                store.link_failure(job['id'], 'failure-1', dict(data, state='resolved', waiver=True))
                self.assertFalse(store.gate(job['id'], sha))
                self.assertFalse(store.gate(job['id'], 'c'*40))
                store.update(job['id'], requirements='Changed acceptance')
                self.assertFalse(store.gate(job['id'], sha))
                self.assertEqual(store.get(job['id'])['external_id'], 'board:1')
            finally:
                store.db.close()


class AcceptanceInputTests(unittest.TestCase):
    def test_requirement_edits_preserve_evidence_but_require_fresh_verdicts(self):
        store = Store(':memory:')
        self.addCleanup(store.db.close)
        job = store.enqueue('board:original', 'project', 'Feature', 'Original criteria')
        sha = 'b'*40
        def validate():
            store.evidence(job['id'], sha, 'qa', {'passed': True})
            store.evidence(job['id'], sha, 'review', dict(approved=True, criteria_met=True,
                simple=True, author_family='openai', reviewer_family='google'))
        store.evidence(job['id'], sha, 'author', {'test_steps': ['exercise criteria']})
        validate()
        self.assertTrue(store.gate(job['id'], sha))
        store.update(job['id'], requirements='Changed criteria')
        self.assertFalse(store.gate(job['id'], sha))
        validate()
        self.assertTrue(store.gate(job['id'], sha))
        self.assertFalse(store.gate(job['id'], 'c'*40))
        self.assertEqual(store.db.execute('select count(*) from evidence').fetchone()[0], 5)

    def test_malformed_dependencies_cannot_disappear(self):
        for requirements in ('Dependency:', 'Dependency: two identities'):
            self.assertTrue(blockers('project', {}, requirements))
        for links in ('qa', [None], [[]]):
            self.assertTrue(blockers('project', {'onboarding_dependencies': links}, ''))
