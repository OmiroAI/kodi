import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator
from kodi import github

REAL_RUN=subprocess.run
REAL_APPROVED_AT=github.approved_at


class MergeTests(unittest.TestCase):
    def test_repository_wait_reconciles_remote_outcome_without_replacing_work(self):
        waiting = self.c.store.enqueue('waiting', 'system', 'Waiting', 'Requirements')
        self.assertIsNone(self.c.store.claim())
        self.pr['headRefOid'] = 'changed'
        self.c.finish_checks()
        self.assertIsNone(self.c.store.claim())
        self.pull.side_effect = RuntimeError('unavailable')
        self.c.finish_prs()
        self.assertIsNone(self.c.store.claim())
        self.pull.side_effect = lambda url: dict(self.pr)
        self.pr['state'] = 'CLOSED'
        self.c.finish_prs()
        self.assertEqual(self.c.store.claim()['id'], waiting['id'])
        self.assertEqual(self.c.store.get(self.job['id'])['pr_url'], self.job['pr_url'])

    def test_registered_workspace_branch_uses_existing_serialized_merge_path(self):
        self.c.store.update(self.job['id'], branch='fix/workspace-change')
        self.pr['headRefName']='fix/workspace-change'
        self.git.side_effect=lambda checkout,*args: ('fix/workspace-change' if args[0]=='symbolic-ref'
            else 'old' if args[0]=='rev-parse' else '')
        self.c.maintain_merges()
        self.merge.assert_called_once_with(self.job['pr_url'],'old')
        self.assertEqual(self.c.store.get(self.job['id'])['state'],'merge_pending')

    def test_registered_workspace_branch_base_refresh_keeps_original_pr_branch(self):
        self.c.store.update(self.job['id'], branch='fix/workspace-change')
        self.pr['headRefName']='fix/workspace-change'
        self.git.side_effect=lambda checkout,*args: ('fix/workspace-change' if args[0]=='symbolic-ref'
            else self.c.store.get(1)['commit_sha'] if args[0]=='rev-parse' else '')
        self.refresh.side_effect=self.rebase
        self.c.maintain_merges()
        self.assertEqual(self.push.call_args.args[2:6],('fix/workspace-change','old','new',1))
        self.assertEqual(self.c.store.get(1)['state'],'ci_pending')
        self.merge.assert_not_called()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = {'source': temp.name, 'github_repo': 'owner/system',
                     'qa_suite': 'system', 'auto_merge': True, 'reviewers': ['maintainer']}
        self.c = Coordinator({'state_dir': temp.name, 'repos': {'system': self.repo}})
        self.addCleanup(self.c.store.db.close)
        self.job = self.add_job('first')
        self.pr = {'state': 'OPEN', 'isDraft': False, 'reviewDecision': 'APPROVED',
                   'headRefName': 'kodi/task-1', 'headRefOid': 'old', 'baseRefName': 'main',
                   'headRepository': {'name': 'system'}, 'headRepositoryOwner': {'login': 'owner'},
                   'autoMergeRequest': None, 'number': 1}
        patches = {
            'approval': patch('kodi.coordinator.github.approved_at', return_value=True),
            'auth': patch('kodi.coordinator.github.authenticated', return_value=True),
            'pull': patch('kodi.coordinator.github.pull', side_effect=lambda url: dict(self.pr)),
            'head': patch('kodi.coordinator.github.remote_head', return_value='old'),
            'git': patch('kodi.coordinator.git', side_effect=self.git),
            'checks': patch('kodi.coordinator.github.check_outcome', return_value='passed'),
            'merge': patch('kodi.coordinator.github.auto_merge'),
            'push': patch('kodi.coordinator.github.refresh_pull'),
            'status': patch('kodi.coordinator.github.independent_status'),
            'source': patch('kodi.coordinator.refresh_source'),
            'ancestry': patch('kodi.coordinator.subprocess.run', return_value=Mock(returncode=0)),
            'refresh': patch.object(self.c, 'refresh_base', side_effect=lambda id: self.c.store.get(id)),
        }
        for name, p in patches.items():
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        state=patch('kodi.coordinator.github.pr_state', side_effect=lambda url: self.pull(url)['state'])
        state.start(); self.addCleanup(state.stop)

    def git(self, checkout, *args):
        if args[0] == 'symbolic-ref': return 'kodi/task-1'
        if args[0] == 'rev-parse': return self.c.store.get(self.job['id'])['commit_sha']
        return ''

    def evidence(self, id, sha):
        self.c.store.evidence(id, sha, 'author', {'summary': 'change', 'test_steps': ['try it']})
        self.c.store.evidence(id, sha, 'qa', {'passed': True})
        self.c.store.evidence(id, sha, 'review', {'approved': True, 'criteria_met': True,
            'simple': True, 'blocking_findings': [], 'author_family': 'openai', 'reviewer_family': 'google'})

    def add_job(self, name):
        job = self.c.store.enqueue(name, 'system', name, 'requirements')
        self.c.store.update(job['id'], state='human_review', commit_sha='old', pr_url='https://github.com/owner/system/pull/'+str(job['id']))
        self.evidence(job['id'], 'old')
        return self.c.store.get(job['id'])

    def test_ci_failure_releases_slot_and_same_sha_rerun_recovers(self):
        self.c.store.update(1,state='ci_pending')
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.checks.return_value='failed'
        self.c.finish_checks()
        self.assertEqual(self.c.store.get(1)['state'],'ci_failed')
        self.add_job('second')
        self.c.config['repos']['other']=dict(self.repo)
        self.checks.return_value='passed'
        self.c.finish_checks()
        self.assertEqual(self.c.store.get(1)['state'],'human_review')
        self.assertIsNone(self.c.store.get(1)['error'])

    def test_failed_ci_without_uncertain_write_allows_another_repository(self):
        self.c.store.update(1,state='ci_pending')
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.checks.return_value='failed'
        self.c.finish_checks()
        self.c.config['repos']['other']=dict(self.repo)
        second=self.c.store.enqueue('second','other','second','requirements')
        self.c.store.update(second['id'],state='human_review',commit_sha='old',pr_url='https://github.com/owner/system/pull/2')
        self.evidence(2,'old')
        self.checks.return_value='passed'
        self.pr['headRefName']='kodi/task-2'
        self.git.side_effect=lambda checkout,*args: 'kodi/task-2' if args[0]=='symbolic-ref' else 'old' if args[0]=='rev-parse' else ''
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(2)['state'],'merge_pending')

    def test_failed_ci_preserves_uncertain_mutation_ownership(self):
        self.c.store.update(1,state='ci_pending')
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.c.store.evidence(1,'old','merge_uncertain',{'operation':'branch_update'})
        self.head.return_value='unconfirmed'
        self.add_job('second')
        self.checks.return_value='failed'
        self.c.finish_checks()
        self.c.maintain_merges()
        self.merge.assert_not_called()
        self.assertIsNone(self.c.store.db.execute("SELECT 1 FROM evidence WHERE kind='merge_released'").fetchone())

    def test_pending_and_unavailable_checks_remain_nonpassing(self):
        for outcome in ('pending','unavailable'):
            self.c.store.update(1,state='ci_pending')
            self.checks.return_value=outcome
            self.c.finish_checks()
            self.assertEqual(self.c.store.get(1)['state'],'ci_pending')

    def test_failed_ci_reconciles_confirmed_update_before_releasing_slot(self):
        self.c.store.update(1,state='ci_pending')
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.c.store.evidence(1,'old','merge_uncertain',{'operation':'branch_update'})
        self.checks.return_value='failed'
        self.head.side_effect=RuntimeError('unavailable')
        self.c.finish_checks()
        self.assertEqual(self.c.store.get(1)['state'],'ci_failed')
        self.assertIsNone(self.c.store.db.execute("SELECT 1 FROM evidence WHERE kind='merge_released'").fetchone())
        self.head.side_effect=None
        self.head.return_value='different'
        self.c.finish_checks()
        self.assertIsNone(self.c.store.db.execute("SELECT 1 FROM evidence WHERE kind='merge_released'").fetchone())
        self.head.return_value='old'
        self.c.finish_checks()
        self.c.finish_checks()
        for kind in ('merge_settled','merge_released'):
            self.assertEqual(self.c.store.db.execute('SELECT COUNT(*) FROM evidence WHERE kind=?',(kind,)).fetchone()[0],1)
        self.c.config['repos']['other']=dict(self.repo)
        second=self.c.store.enqueue('second','other','second','requirements')
        self.c.store.update(second['id'],state='human_review',commit_sha='old',pr_url='https://github.com/owner/system/pull/2')
        self.evidence(second['id'],'old')
        self.checks.return_value='passed'
        self.pr['headRefName']='kodi/task-2'
        self.git.side_effect=lambda checkout,*args: 'kodi/task-2' if args[0]=='symbolic-ref' else 'old' if args[0]=='rev-parse' else ''
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(2)['state'],'merge_pending')

    def test_closed_ci_pending_releases_slot_before_gate(self):
        self.c.store.update(1,state='ci_pending',commit_sha='unvalidated')
        self.c.store.db.execute('DELETE FROM evidence WHERE job=1')
        self.c.store.db.commit()
        self.assertFalse(self.c.store.gate(1,'unvalidated'))
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.add_job('second')
        self.pull.side_effect=lambda url: dict(self.pr,state='CLOSED' if url.endswith('/1') else 'OPEN',headRefName='kodi/task-2')
        self.git.side_effect=lambda checkout,*args: 'kodi/task-2' if args[0]=='symbolic-ref' else 'old' if args[0]=='rev-parse' else ''
        self.c.finish_checks()
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'],'cancelled')
        self.assertEqual(self.c.store.get(2)['state'],'merge_pending')

    def test_failed_ci_reconciles_write_without_passing_gate_or_checks(self):
        for outcome in (None,'pending','unavailable'):
            with self.subTest(outcome=outcome):
                self.c.store.update(1,state='ci_failed',commit_sha='unvalidated')
                if outcome is not None:
                    self.evidence(1,'unvalidated')
                self.pr['headRefOid']='unvalidated'
                self.c.store.evidence(1,'unvalidated','merge_maintenance',{})
                self.c.store.evidence(1,'unvalidated','merge_uncertain',{'operation':'branch_update'})
                self.head.return_value='unconfirmed'
                self.checks.return_value=outcome
                self.c.finish_checks()
                released=self.c.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='merge_released'").fetchone()[0]
                self.head.return_value='unvalidated'
                self.c.finish_checks()
                self.c.finish_checks()
                self.assertEqual(self.c.store.get(1)['state'],'ci_failed')
                self.assertEqual(self.c.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='merge_released'").fetchone()[0],released+1)
        self.assertEqual(self.checks.call_count,6)

    def test_check_reader_reconciles_terminal_pr_when_lifecycle_lookup_fails(self):
        for remote,expected in (('CLOSED','cancelled'),('MERGED','done')):
            with self.subTest(remote=remote):
                self.c.store.update(1,state='ci_pending',commit_sha='unvalidated')
                self.pr['state']=remote
                with patch('kodi.coordinator.github.pr_state',side_effect=RuntimeError('unavailable')):
                    self.c.finish_prs()
                self.c.finish_checks()
                self.assertEqual(self.c.store.get(1)['state'],expected)
        self.checks.assert_not_called()

    def test_check_reader_retains_uncertain_update_until_remote_reconciles(self):
        self.c.store.update(1,state='ci_pending',commit_sha='unvalidated')
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.c.store.evidence(1,'new','merge_uncertain',{'operation':'branch_update'})
        self.pr['state']='CLOSED'
        self.c.finish_checks()
        self.assertEqual(self.c.store.get(1)['state'],'ci_pending')
        self.head.return_value='new'
        self.c.finish_checks()
        self.assertEqual(self.c.store.get(1)['state'],'cancelled')
        self.checks.assert_not_called()

    def test_check_reader_reconciles_closure_during_check_inspection(self):
        self.c.store.update(1,state='ci_pending')
        self.pull.side_effect=[dict(self.pr),dict(self.pr,state='CLOSED')]
        self.c.finish_checks()
        self.assertEqual(self.c.store.get(1)['state'],'cancelled')

    def test_closed_uncertain_update_retains_slot_until_remote_reconciles(self):
        self.c.store.update(1,state='merge_updating')
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.c.store.evidence(1,'new','merge_uncertain',{'operation':'branch_update'})
        self.add_job('second')
        self.pull.side_effect=lambda url: dict(self.pr,state='CLOSED' if url.endswith('/1') else 'OPEN')
        self.c.finish_prs()
        self.assertEqual(self.c.store.get(1)['state'],'merge_updating')
        self.c.maintain_merges()
        self.merge.assert_not_called()
        self.head.return_value='new'
        self.c.finish_prs()
        self.assertEqual(self.c.store.get(1)['state'],'cancelled')
        self.assertIsNotNone(self.c.store.db.execute("SELECT 1 FROM evidence WHERE kind='merge_settled'").fetchone())

    def test_terminal_update_without_uncertain_write_reconciles_before_gate(self):
        for remote,state in (('CLOSED','cancelled'),('MERGED','done')):
            with self.subTest(remote=remote):
                self.c.store.update(1,state='merge_updating',commit_sha='unvalidated')
                self.pr['state']=remote
                self.c.finish_prs()
                self.assertEqual(self.c.store.get(1)['state'],state)

    def test_unowned_ci_wait_allows_another_repository(self):
        self.c.config['repos']['other']=dict(self.repo)
        second=self.c.store.enqueue('second','other','second','requirements')
        self.c.store.update(second['id'],state='ci_pending',commit_sha='old',
            pr_url='https://github.com/owner/system/pull/2')
        self.evidence(second['id'],'old')
        self.c.maintain_merges()
        self.merge.assert_called_once_with(self.job['pr_url'],'old')
        self.assertEqual(self.c.store.get(second['id'])['state'],'ci_pending')

    def test_owned_ci_wait_retains_slot(self):
        self.c.store.update(1,state='ci_pending')
        self.c.store.evidence(1,'old','merge_maintenance',{})
        self.add_job('second')
        self.c.maintain_merges()
        self.refresh.assert_not_called()
        self.merge.assert_not_called()

    def test_configured_human_scenarios_are_independent_of_telegram(self):
        sha='a'*40
        self.approval.side_effect=REAL_APPROVED_AT
        self.c.store.update(1,commit_sha=sha)
        self.evidence(1,sha)
        self.pr['headRefOid']=sha
        self.head.return_value=sha
        approved={'state':'APPROVED','commit_id':sha,
                  'user':{'login':'maintainer','type':'User'}}
        scenarios=[('correct',[approved],True),
            ('wrong human',[dict(approved,user={'login':'other','type':'User'})],False),
            ('bot',[dict(approved,user={'login':'maintainer','type':'Bot'})],False),
            ('stale',[dict(approved,commit_id='b'*40)],False),
            ('dismissed',[approved,dict(approved,state='DISMISSED')],False),
            ('missing',[],False)]
        for enabled in (False,True):
            self.c.config['telegram']={'enabled':enabled}
            for name,reviews,eligible in scenarios:
                with self.subTest(telegram=enabled,scenario=name):
                    self.c.store.update(1,state='human_review')
                    self.merge.reset_mock()
                    with patch('kodi.github.api_pages',return_value=[reviews]):
                        self.c.maintain_merges()
                    self.assertEqual(self.merge.called,eligible)

    def test_reviewer_configuration_override_fallback_and_invalid_values(self):
        for enabled in (False,True):
            self.c.config['telegram']={'enabled':enabled}
            self.c.config['reviewers']=['global-reviewer']
            self.repo.pop('reviewers',None)
            self.assertTrue(self.c.human_approved(self.job,self.pr))
            self.assertEqual(self.approval.call_args.args[2],['global-reviewer'])
            self.repo['reviewers']=['repository-reviewer']
            self.assertTrue(self.c.human_approved(self.job,self.pr))
            self.assertEqual(self.approval.call_args.args[2],['repository-reviewer'])
            for invalid in ([],None,'maintainer',[1],['bad login']):
                self.repo['reviewers']=invalid
                self.c.store.update(1,state='human_review')
                self.c.maintain_merges()
                self.assertEqual(self.c.store.get(1)['state'],'blocked')
                self.assertIn('reviewers list',self.c.store.get(1)['error'])
            self.repo.pop('reviewers')
            self.c.config.pop('reviewers')
            self.c.store.update(1,state='human_review')
            self.c.maintain_merges()
            self.assertEqual(self.c.store.get(1)['state'],'blocked')
        self.merge.assert_not_called()
        self.push.assert_not_called()

    def test_exact_approval_rechecked_before_refresh_and_merge(self):
        for enabled in (False,True):
            self.c.config['telegram']={'enabled':enabled}
            for rebase,approvals in ((True,[True,True]),(True,[True,False]),
                                     (False,[True,True,False])):
                with self.subTest(telegram=enabled,rebase=rebase,approvals=approvals):
                    self.c.store.update(1,state='human_review',commit_sha='old')
                    self.push.reset_mock();self.merge.reset_mock();self.approval.reset_mock()
                    self.approval.side_effect=approvals
                    self.refresh.side_effect=self.rebase if rebase else lambda id:self.c.store.get(id)
                    self.c.maintain_merges()
                    self.assertEqual(self.push.called,rebase and approvals[-1])
                    self.merge.assert_not_called()
                    self.assertTrue(all(call.args[1]=='old' for call in self.approval.call_args_list))

    def test_refreshed_candidate_requires_new_exact_approval(self):
        self.refresh.side_effect=self.rebase
        self.c.maintain_merges()
        self.pr['headRefOid']='new';self.head.return_value='new'
        self.c.finish_checks()
        self.approval.return_value=False
        self.c.maintain_merges()
        self.approval.assert_called_with(self.job['pr_url'],'new',['maintainer'])
        self.merge.assert_not_called()

    def test_disabled_does_not_read_or_mutate_github(self):
        for value in (None, False, 'true'):
            self.repo['auto_merge'] = value
            self.c.maintain_merges()
        self.pull.assert_not_called()
        self.merge.assert_not_called()

    def test_no_approval_or_draft_never_updates_or_merges(self):
        for changes in ({'reviewDecision': 'REVIEW_REQUIRED'}, {'reviewDecision': None}, {'isDraft': True}):
            self.pr.update(changes)
            self.c.maintain_merges()
        self.refresh.assert_not_called()
        self.push.assert_not_called()
        self.merge.assert_not_called()

    def test_mismatches_and_dirty_checkout_block_preserving_budget(self):
        for changes in ({'headRefOid': 'external'}, {'headRefName': 'external'},
                        {'baseRefName': 'release'}, {'headRepositoryOwner': {'login': 'other'}}):
            with self.subTest(changes=changes):
                original = dict(self.pr)
                self.pr.update(changes)
                self.c.store.update(1, state='human_review')
                self.c.maintain_merges()
                self.assertEqual(self.c.store.get(1)['state'], 'blocked')
                self.pr = original
        self.head.return_value = 'external'
        self.c.store.update(1, state='human_review')
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.head.return_value = 'old'
        self.git.side_effect = lambda checkout, *args: 'dirty' if args[0]=='status' else ('old' if args[0]=='rev-parse' else 'kodi/task-1')
        self.c.store.update(1, state='human_review')
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.assertEqual(self.c.store.get(1)['attempt'], 0)
        self.refresh.assert_not_called()
        self.merge.assert_not_called()

    def test_matching_external_pr_and_remote_head_reject_unrecorded_sha(self):
        self.pr['headRefOid'] = 'external'
        self.head.return_value = 'external'
        with self.assertRaisesRegex(RuntimeError, 'PR head'):
            self.c.validate_merge_head(self.job, self.pr, 'external')
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.assertEqual(self.c.store.get(1)['commit_sha'], 'old')
        self.assertEqual(self.c.store.get(1)['attempt'], 0)
        self.assertTrue(self.c.store.gate(1, 'old'))
        self.refresh.assert_not_called()
        self.push.assert_not_called()
        self.merge.assert_not_called()

    def rebase(self, id):
        self.c.store.update(id, commit_sha='new', state='validated')
        self.evidence(id, 'new')
        return self.c.store.get(id)

    def test_behind_base_updates_exact_sha_body_then_waits_for_ci_and_new_approval(self):
        self.refresh.side_effect = self.rebase
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'], 'ci_pending')
        args = self.push.call_args.args
        self.assertEqual(args[2:6], ('kodi/task-1', 'old', 'new', 1))
        self.assertIn('Verified commit: `new`', args[6])
        self.assertIn('1. try it', args[6])
        self.status.assert_called_once_with('owner/system', 'new', self.job['pr_url'])
        self.c.maintain_merges()
        self.merge.assert_not_called()
        self.pr.update(headRefOid='new', reviewDecision='REVIEW_REQUIRED')
        self.head.return_value = 'new'
        self.c.finish_checks()
        self.c.maintain_merges()
        self.merge.assert_not_called()
        self.refresh.assert_called_once()

    def test_ci_wait_and_approval_race_prevent_merge(self):
        self.checks.return_value = 'pending'
        self.c.maintain_merges()
        self.merge.assert_not_called()

        self.checks.return_value = 'passed'
        self.pull.side_effect = [dict(self.pr), dict(self.pr), dict(self.pr, reviewDecision='CHANGES_REQUESTED')]
        self.c.maintain_merges()
        self.merge.assert_not_called()

    def test_main_advancing_during_checks_defers_request(self):
        self.ancestry.return_value = Mock(returncode=1)
        self.c.maintain_merges()
        self.merge.assert_not_called()
        self.assertIsNotNone(self.c.store.db.execute(
            "SELECT 1 FROM evidence WHERE kind='merge_maintenance'").fetchone())

    def test_changed_head_while_waiting_for_ci_blocks(self):
        self.c.store.update(1, state='ci_pending')
        self.pr['headRefOid'] = 'external'
        self.c.finish_checks()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.merge.assert_not_called()

    def test_unreadable_pending_merge_blocks_with_deduplicated_notice(self):
        self.c.store.update(1, state='merge_pending')
        self.c.store.evidence(1, 'old', 'merge_maintenance', {'expected_head': 'old'})
        self.c.config['telegram_env'] = 'unused-offline'
        self.pull.side_effect = RuntimeError('GitHub unavailable')
        with patch('kodi.coordinator.send') as send:
            self.assertFalse(self.c.finish_prs())
            self.assertTrue(self.c.finish_prs())
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')

    def test_interrupted_update_is_blocked_without_republishing(self):
        self.c.store.evidence(1, 'old', 'merge_maintenance', {'expected_head': 'old'})
        self.c.store.update(1, state='merge_updating')
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.push.assert_not_called()
        self.merge.assert_not_called()

    def test_armed_request_still_refreshes_when_main_advances(self):
        self.c.store.evidence(1, 'old', 'merge_maintenance', {'expected_head': 'old'})
        self.c.store.update(1, state='merge_pending')
        self.pr['autoMergeRequest']={'enabledAt':'earlier'}
        self.refresh.side_effect=self.rebase
        self.c.maintain_merges()
        self.refresh.assert_called_once_with(1)
        self.push.assert_called_once()
        self.assertEqual(self.c.store.get(1)['state'],'ci_pending')
        self.merge.assert_not_called()

    def test_ticks_cannot_overlap_and_lock_releases_after_completion(self):
        other=Coordinator(self.c.config)
        self.addCleanup(other.store.db.close)
        with patch.object(other,'_tick') as run_other, patch.object(self.c,'_tick',side_effect=other.tick):
            self.c.tick()
            run_other.assert_not_called()
            other.tick()
            run_other.assert_called_once()

    def test_completed_async_rebase_keeps_original_remote_lease(self):
        from kodi.store import Store
        self.c.store.evidence(1, 'old', 'merge_maintenance', {'expected_head': 'old'})
        token = self.c.store.begin_invocation(1, 'base')
        worker = Store(Path(self.c.config['state_dir'])/'kodi.sqlite', token)
        self.addCleanup(worker.db.close)
        worker.update(1, commit_sha='new', state='validated')
        # Fixture evidence represents a completed independent worker exchange.
        self.evidence(1, 'new')
        with worker.db:
            worker.db.execute("UPDATE invocations SET status='complete' WHERE id=?", (token,))
        self.c.maintain_merges()
        self.assertEqual(self.push.call_args.args[3:5], ('old', 'new'))
        self.merge.assert_not_called()

    def test_two_mutation_cycles_cannot_overlap(self):
        other = Coordinator(self.c.config)
        self.addCleanup(other.store.db.close)
        with patch.object(other, '_tick') as competing, patch.object(self.c, '_tick',
                side_effect=self.c.maintain_merges):
            self.merge.side_effect = lambda *args: other.tick()
            self.c.tick()
            competing.assert_not_called()
            self.merge.assert_called_once()
            other.tick()
            competing.assert_called_once()

    def test_exact_gate_required(self):
        self.c.store.update(1, commit_sha='unverified')
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.merge.assert_not_called()

    def test_active_delivery_serializes_updates(self):
        second = self.add_job('second')
        for state in ('publishing',):
            self.c.store.update(second['id'], state=state)
            self.c.maintain_merges()
        self.refresh.assert_not_called()

    def test_unrelated_worker_does_not_prevent_approved_merge(self):
        second = self.add_job('second')
        self.c.store.update(second['id'], state='coding')
        self.c.maintain_merges()
        self.merge.assert_called_once_with(self.job['pr_url'], 'old')

    def test_unmarked_pending_merge_or_update_also_holds_serial_slot(self):
        second=self.add_job('second')
        for state in ('merge_pending','merge_updating'):
            self.c.store.update(second['id'],state=state)
            self.c.maintain_merges()
        self.refresh.assert_not_called()
        self.merge.assert_not_called()

    def test_pending_request_is_retry_safe_and_terminal_releases_slot(self):
        self.add_job('second')
        self.c.maintain_merges()
        self.merge.assert_called_once_with(self.job['pr_url'], 'old')
        self.assertEqual(self.c.store.get(1)['state'], 'merge_pending')
        self.pr['autoMergeRequest'] = {'enabledAt': 'now'}
        self.c.maintain_merges()
        self.merge.assert_called_once()
        self.assertEqual(self.c.store.get(2)['state'], 'human_review')
        self.pull.side_effect = lambda url: dict(self.pr, state='MERGED' if url.endswith('/1') else 'CLOSED')
        self.assertTrue(self.c.finish_prs())
        self.assertEqual(self.c.store.get(1)['state'], 'done')
        self.assertEqual(self.c.store.get(2)['state'], 'cancelled')

    def test_failure_blocks_and_serial_slot_is_retained(self):
        self.add_job('second')
        self.refresh.side_effect = RuntimeError('base conflict')
        self.c.maintain_merges()
        self.assertIn('Inspect private evidence',self.c.store.get(1)['error'])
        self.c.maintain_merges()
        self.refresh.assert_called_once()
        self.merge.assert_not_called()

    def test_delivery_failure_after_native_merge_still_reconciles_terminal_state(self):
        self.c.config['telegram_env']='unused-offline'
        with patch('kodi.coordinator.send',side_effect=[RuntimeError('delivery failed'),None,None]):
            self.c.maintain_merges()
            self.assertEqual(self.c.store.get(1)['state'],'blocked')
            self.pr['state']='MERGED'
            self.c.finish_prs()
        self.assertEqual(self.c.store.get(1)['state'],'done')
        self.merge.assert_called_once()

    def test_approval_lost_during_rebase_preserves_candidate_without_push(self):
        self.refresh.side_effect = self.rebase
        self.pull.side_effect = [dict(self.pr), dict(self.pr, reviewDecision='REVIEW_REQUIRED')]
        self.c.maintain_merges()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.assertIn('Inspect private evidence',self.c.store.get(1)['error'])
        self.assertTrue(self.c.store.gate(1, 'old'))
        self.assertTrue(self.c.store.gate(1, 'new'))
        self.push.assert_not_called()
        self.merge.assert_not_called()

    def test_real_rebased_checkout_and_evidence_survive_revoked_approval(self):
        self.ancestry.side_effect=REAL_RUN
        source=Path(self.repo['source'])/'source';source.mkdir()
        checkout=Path(self.c.config['state_dir'])/'jobs/1/checkout'
        checkout.parent.mkdir(parents=True)
        def git_at(directory,*args):
            return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',
                '-c','user.name=Test','-c','user.email=test@example.invalid',*args],
                cwd=directory,text=True,stderr=subprocess.DEVNULL).strip()
        git_at(source,'init','-b','main')
        (source/'baseline').write_text('baseline');git_at(source,'add','.');git_at(source,'commit','-m','baseline')
        git_at(source,'clone','--no-local','--no-hardlinks',str(source),str(checkout))
        git_at(checkout,'checkout','-b','kodi/task-1')
        (checkout/'feature').write_text('candidate');git_at(checkout,'add','.');git_at(checkout,'commit','-m','feature')
        original=git_at(checkout,'rev-parse','HEAD')
        (source/'upstream').write_text('trusted main');git_at(source,'add','.');git_at(source,'commit','-m','advance main')
        base=git_at(source,'rev-parse','HEAD')
        self.repo['source']=str(source)
        self.c.store.update(1,commit_sha=original);self.evidence(1,original)
        self.pr['headRefOid']=original;self.head.return_value=original
        self.pull.side_effect=[dict(self.pr),dict(self.pr,reviewDecision='REVIEW_REQUIRED')]
        def record_review(job,path,sha,author):
            verdict={'approved':True,'criteria_met':True,'simple':True,'blocking_findings':[],
                     'author_family':'openai','reviewer_family':'google'}
            self.c.store.evidence(job['id'],sha,'review',verdict)
            return verdict
        with patch('kodi.coordinator.git',side_effect=git_at), \
                patch('kodi.coordinator.subprocess.run',new=REAL_RUN), \
                patch.object(self.c,'refresh_base',side_effect=lambda id:Coordinator.refresh_base(self.c,id)), \
                patch('kodi.coordinator.qa',side_effect=lambda config, checkout, sha, suite: {'passed':True, 'sha':sha}), \
                patch.object(self.c,'review',side_effect=record_review):
            self.c.maintain_merges()
        preserved=git_at(checkout,'rev-parse','HEAD')
        job=self.c.store.get(1)
        self.assertNotEqual(preserved,original)
        self.assertEqual(job['commit_sha'],preserved)
        self.assertEqual(job['state'],'blocked')
        self.assertEqual(git_at(checkout,'rev-parse','main'),base)
        self.assertEqual((checkout/'feature').read_text(),'candidate')
        self.assertEqual(git_at(checkout,'status','--porcelain'),'')
        self.assertTrue(self.c.store.gate(1,original))
        self.assertTrue(self.c.store.gate(1,preserved))
        self.push.assert_not_called();self.merge.assert_not_called()


class GitHubMergeCommandsTests(unittest.TestCase):
    def test_native_merge_is_pinned_and_has_no_bypass(self):
        with patch('kodi.github.gh') as gh:
            github.auto_merge('url', 'sha')
        self.assertEqual(gh.call_args.args, ('pr', 'merge', 'url', '--auto', '--squash', '--match-head-commit', 'sha'))

    def test_refresh_uses_explicit_lease_and_rest_patch(self):
        with patch('kodi.github.environment', return_value={}), patch('kodi.github.subprocess.run', return_value=Mock(returncode=0)) as run, patch('kodi.github.gh') as gh:
            github.refresh_pull('owner/system', Path('/tmp/checkout'), 'kodi/task-1', 'old', 'new', 1, 'testing evidence')
        command = run.call_args.args[0]
        self.assertIn('--force-with-lease=refs/heads/kodi/task-1:old', command)
        self.assertIn('new:refs/heads/kodi/task-1', command)
        self.assertIn('https://github.com/owner/system.git', command)
        self.assertEqual(gh.call_args.args[:4], ('api', '--method', 'PATCH', 'repos/owner/system/pulls/1'))
        self.assertEqual(json.loads(gh.call_args.kwargs['stdin']), {'body': 'testing evidence'})

    def test_lease_failure_does_not_patch(self):
        with patch('kodi.github.environment', return_value={}), patch('kodi.github.subprocess.run', return_value=Mock(returncode=1)), patch('kodi.github.gh') as gh:
            with self.assertRaises(RuntimeError):
                github.refresh_pull('owner/system', Path('/tmp/checkout'), 'kodi/task-1', 'old', 'new', 1, 'body')
        gh.assert_not_called()
