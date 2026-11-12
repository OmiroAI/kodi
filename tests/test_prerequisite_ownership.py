"""Admission must preserve retained work while allowing explicit prerequisites."""
import unittest
from pathlib import Path
from unittest.mock import Mock

from kodi import capacity

import test_sync


class PrerequisiteOwnershipTests(unittest.TestCase):
    setUp = test_sync.SyncTests.setUp

    def jobs(self):
        store = self.c.store
        prerequisite = store.enqueue('issue:prerequisite', 'project', 'Prepare', '')
        dependent = store.enqueue('issue:dependent', 'project', 'Feature',
                                  'Dependency: issue:prerequisite')
        store.update(dependent['id'], state='blocked', attempt=2,
                     commit_sha='a'*40, branch='retained-branch', pr_url='example:pr')
        store.evidence(dependent['id'], 'a'*40, 'author', {'test_steps': ['manual']})
        return prerequisite, store.get(dependent['id'])

    def claim(self):
        return self.c.store.claim(eligible=lambda job: not self.c.dependency_blockers(job),
                                  coding_limit=1)

    def test_prerequisite_progress_preserves_dependent_and_wait_reason(self):
        prerequisite, dependent = self.jobs()
        store = self.c.store
        checkout = Path(self.directory.name)/'jobs'/str(dependent['id'])/'checkout'
        checkout.mkdir(parents=True)
        (checkout/'retained.txt').write_text('preserved work')
        before = dict(dependent)
        evidence = list(map(tuple, store.db.execute('SELECT * FROM evidence WHERE job=?',
                                                   (dependent['id'],))))
        self.assertIn('waiting for prerequisite task', self.c.dependency_blockers(dependent)[0])
        self.assertEqual(self.claim()['id'], prerequisite['id'])
        self.assertEqual(dict(store.get(dependent['id'])), before)
        self.assertEqual((checkout/'retained.txt').read_text(), 'preserved work')
        self.assertEqual(list(map(tuple, store.db.execute('SELECT * FROM evidence WHERE job=?',
                                                         (dependent['id'],)))), evidence)
        self.assertEqual(store.repository_blocker(dependent)['id'], prerequisite['id'])
        store.update(prerequisite['id'], state='done')
        self.assertEqual(self.c.dependency_blockers(dependent), [])

    def test_transitive_prerequisite_and_status_projection(self):
        prerequisite, dependent = self.jobs()
        store = self.c.store
        middle = store.enqueue('issue:middle', 'project', 'Intermediate',
                               'Dependency: issue:prerequisite')
        store.update(dependent['id'], requirements='Dependency: issue:middle', board_id='board-1')
        self.task['description'] = 'Dependency: issue:middle'
        self.c.progress(store.get(dependent['id']))
        description = self.c.board.description.call_args.args[1]
        self.assertIn('Dependencies: issue:middle: waiting for prerequisite task', description)
        self.assertIn('(queued)', description)
        self.assertEqual(self.claim()['id'], prerequisite['id'])

    def test_running_uncertain_and_delivery_ownership_block(self):
        for kind in ('running', 'capacity', 'merge_uncertain', 'merge_maintenance', 'publishing'):
            with self.subTest(kind=kind):
                prerequisite, dependent = self.jobs()
                store = self.c.store
                if kind == 'running':
                    store.begin_invocation(dependent['id'], 'coding')
                elif kind == 'capacity':
                    store.update(dependent['id'], state='coding')
                    capacity.acquire(store, dependent['id'], 'coding', 'codex',
                                     {'stages': {'coding': 1}, 'providers': {'codex': 1}}, 'fixture')
                    store.update(dependent['id'], state='blocked')
                    store.db.execute('UPDATE capacity_claims SET lease=0')
                    store.db.commit()
                elif kind == 'publishing':
                    store.update(dependent['id'], state=kind)
                else:
                    store.evidence(dependent['id'], '', kind, {})
                self.assertEqual(store.repository_blocker(prerequisite)['id'], dependent['id'])
                self.assertIsNone(self.claim())
                store.db.execute('DELETE FROM capacity_claims')
                store.db.execute('DELETE FROM invocations')
                store.db.execute('DELETE FROM evidence')
                store.db.execute('DELETE FROM jobs')
                store.db.commit()

    def test_cycle_unrelated_and_cross_repository_cannot_bypass(self):
        prerequisite, dependent = self.jobs()
        store = self.c.store
        unrelated = store.enqueue('issue:unrelated', 'project', 'Other', '')
        self.assertEqual(store.repository_blocker(unrelated)['id'], dependent['id'])
        store.update(prerequisite['id'], requirements='Dependency: issue:dependent')
        self.assertIn('cycle', self.c.dependency_blockers(store.get(prerequisite['id']))[0])
        self.assertEqual(store.repository_blocker(prerequisite)['id'], dependent['id'])
        self.assertIsNone(self.claim())
        store.update(dependent['id'], requirements='Dependency: missing')
        self.assertEqual(store.repository_blocker(prerequisite)['id'], dependent['id'])
        foreign = store.enqueue('issue:foreign', 'other', 'Other repository', '')
        store.update(dependent['id'], requirements='Dependency: issue:foreign')
        self.assertEqual(store.task_dependencies(store.get(dependent['id'])), [])

    def test_disabled_repository_and_stage_capacity_still_block(self):
        prerequisite, dependent = self.jobs()
        self.c.config['repos']['project']['enabled'] = False
        self.assertIsNone(self.claim())
        self.c.config['repos']['project']['enabled'] = True
        other = self.c.store.enqueue('issue:other', 'other', 'Active', '')
        self.c.store.update(other['id'], state='coding')
        self.assertIsNone(self.claim())

    def test_non_ready_prerequisite_is_not_claimed(self):
        prerequisite, dependent = self.jobs()
        self.c.store.update(prerequisite['id'], board_id='board-1')
        self.c.config['capacity'] = {'stages': {'coding': 1, 'qa': 1, 'review': 1},
                                     'providers': {'codex': 1}}
        for method in ('finish_prs', 'sync', 'finish_checks', 'request_reviews', 'maintain_merges', 'progress'):
            setattr(self.c, method, Mock())
        self.c.process = Mock()
        self.task['workflow_step_id'] = 'backlog'
        self.c._tick()
        self.c.process.assert_not_called()
        self.assertEqual(self.c.store.get(prerequisite['id'])['state'], 'queued')
        self.task['workflow_step_id'] = 'ready'
        self.c._tick()
        self.assertEqual(self.c.process.call_args.args[0]['id'], prerequisite['id'])
