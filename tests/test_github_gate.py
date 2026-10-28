import json
import unittest
from unittest.mock import MagicMock, patch

from kodi.github import checks_pass, check_outcome

SHA='a'*40
URL='https://github.com/example/repo/pull/1'
RUN={'name':'system-tests','head_sha':SHA,'status':'completed','conclusion':'success'}
STATUS={'context':'kodi/independent-acceptance','state':'success'}


class GitHubGateTests(unittest.TestCase):
    def test_explicit_check_outcomes(self):
        for runs,statuses,outcome in (([RUN],[STATUS],'passed'),
                ([],[STATUS],'pending'),
                ([{**RUN,'status':'in_progress','conclusion':None}],[STATUS],'pending'),
                ([{**RUN,'conclusion':'failure'}],[STATUS],'failed'),
                ([RUN],[{**STATUS,'state':'error'}],'failed')):
            with self.subTest(outcome=outcome), patch('kodi.github.gh',side_effect=[
                    json.dumps({'head':{'sha':SHA}}),json.dumps({'check_runs':runs}),
                    json.dumps({'statuses':statuses})]):
                self.assertEqual(check_outcome(URL,SHA,['system-tests','kodi/independent-acceptance']),outcome)
        with patch('kodi.github.gh',side_effect=RuntimeError('private diagnostic')):
            self.assertEqual(check_outcome(URL,SHA,[]),'unavailable')

    def test_only_explicitly_optional_release_job_can_be_skipped(self):
        values=[json.dumps({'head':{'sha':SHA}}),json.dumps({'check_runs':[
            RUN,{'name':'release','head_sha':SHA,'status':'completed','conclusion':'skipped'}]}),
            json.dumps({'statuses':[STATUS]})]
        with patch('kodi.github.gh',side_effect=values):
            self.assertTrue(checks_pass(URL,SHA,['system-tests','kodi/independent-acceptance'],['release']))
        with self.assertRaises(ValueError):checks_pass(URL,SHA,['system-tests'],['system-tests'])
    def check(self,sha=SHA,runs=None,statuses=None):
        values=[json.dumps({'head':{'sha':sha}}),json.dumps({'check_runs':runs if runs is not None else [RUN]}),
                json.dumps({'statuses':statuses if statuses is not None else [STATUS]})]
        with patch('kodi.github.gh',side_effect=values) as call:
            result=checks_pass(URL,SHA,['system-tests','kodi/independent-acceptance'])
            self.calls=call.call_args_list
            return result

    def test_private_ci_permission_failure_explains_setup_without_leaking_stderr(self):
        failure=MagicMock(returncode=1,stderr='Resource not accessible by integration secret-diagnostic')
        success=MagicMock(returncode=0,stdout=json.dumps({'head':{'sha':SHA}}))
        with patch('kodi.github.environment',return_value={}), \
                patch('kodi.github.subprocess.run',side_effect=[success,failure]):
            with self.assertRaises(RuntimeError) as error: checks_pass(URL,SHA,[])
        self.assertIn('Checks Read-only in Permissions & events',str(error.exception))
        self.assertIn('accept updated installation permissions',str(error.exception))
        self.assertNotIn('secret-diagnostic',str(error.exception))

    def test_independent_status_alone_does_not_replace_github_ci(self):
        self.assertFalse(self.check(runs=[]))

    def test_pending_skipped_or_stale_ci_blocks_ready(self):
        self.assertFalse(self.check(sha='b'*40))
        self.assertEqual(len(self.calls),1)
        for change in [{'conclusion':'skipped'},{'conclusion':None},{'status':'in_progress'},
                       {'head_sha':'b'*40},{'conclusion':'failure'}]:
            with self.subTest(change=change): self.assertFalse(self.check(runs=[{**RUN,**change}]))
        self.assertFalse(self.check(statuses=[{**STATUS,'state':'pending'}]))

    def test_both_current_commit_checks_pass_without_workflow_access(self):
        self.assertTrue(self.check())
        self.assertTrue(all(call.args[0]=='api' for call in self.calls))
        self.assertNotIn('statusCheckRollup',str(self.calls))

    def test_all_pages_are_checked_for_required_names_and_failures(self):
        def check(last_status):
            with patch('kodi.github.gh',side_effect=[json.dumps({'head':{'sha':SHA}}),
                    json.dumps({'check_runs':[]})+'\n'+json.dumps({'check_runs':[RUN]}),
                    json.dumps({'statuses':[STATUS]})+'\n'+json.dumps({'statuses':[last_status]})]) as call:
                result=checks_pass(URL,SHA,['system-tests','kodi/independent-acceptance'])
                self.assertIn('--paginate',call.call_args_list[1].args)
                self.assertNotIn('--slurp',call.call_args_list[2].args)
                return result
        self.assertTrue(check({'context':'extra','state':'success'}))
        self.assertFalse(check({'context':'extra','state':'failure'}))

    def test_invalid_destination_or_sha_never_calls_github(self):
        for url,sha in [('https://evil.test/example/repo/pull/1',SHA),(URL,'main'),
                        ('https://github.com/example/repo/pull/1?x=y',SHA)]:
            with patch('kodi.github.gh') as call,self.assertRaises(ValueError):checks_pass(url,sha,[])
            call.assert_not_called()
