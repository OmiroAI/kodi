import json
import unittest
from unittest.mock import MagicMock, patch

from kodi.github import checks_pass

SHA='a'*40
URL='https://github.com/example/repo/pull/1'
RUN={'name':'system-tests','head_sha':SHA,'status':'completed','conclusion':'success'}
STATUS={'context':'kodi/independent-acceptance','state':'success'}


class GitHubGateTests(unittest.TestCase):
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

    def test_terminal_diagnostics_do_not_infer_root_cause(self):
        for conclusion, terminal in [('failure', True), ('timed_out', True),
                                     ('skipped', False), (None, False)]:
            diagnostics = {}
            values = [json.dumps({'head': {'sha': SHA}}),
                      json.dumps({'check_runs': [{**RUN, 'conclusion': conclusion}]}),
                      json.dumps({'statuses': [STATUS]})]
            with patch('kodi.github.gh', side_effect=values):
                self.assertFalse(checks_pass(URL, SHA, ['system-tests'], diagnostics=diagnostics))
            self.assertEqual(diagnostics, {'terminal': terminal, **({'failure_signal': 'execution_deadline'} if conclusion == 'timed_out' else {})})

    def test_explicit_ci_unavailability_is_sanitized_and_never_passes(self):
        for change, signal in [
                ({'conclusion': 'skipped', 'output': {'title': 'Workflow is disabled',
                                                     'text': 'secret-sentinel'}}, 'ci_disabled'),
                ({'conclusion': 'startup_failure'}, 'ci_outage')]:
            diagnostics = {}
            values = [json.dumps({'head': {'sha': SHA}}),
                      json.dumps({'check_runs': [{**RUN, **change}]}),
                      json.dumps({'statuses': [STATUS]})]
            with patch('kodi.github.gh', side_effect=values):
                self.assertFalse(checks_pass(URL, SHA, ['system-tests'], diagnostics=diagnostics))
            self.assertEqual(diagnostics, {'terminal': True, 'failure_signal': signal})
            self.assertNotIn('secret-sentinel', json.dumps(diagnostics))

    def test_specific_ci_evidence_survives_generic_failures(self):
        for conclusion, signal in [('startup_failure', 'ci_outage'),
                                   ('timed_out', 'execution_deadline')]:
            for generic in ('run', 'status', 'both'):
                for reverse in (False, True):
                    with self.subTest(conclusion=conclusion, generic=generic, reverse=reverse):
                        runs = [{**RUN, 'conclusion': conclusion,
                                 'output': {'text': 'secret-sentinel'}}]
                        statuses = [STATUS]
                        if generic in ('run', 'both'):
                            runs.append({**RUN, 'name': 'other', 'conclusion': 'failure'})
                        if generic in ('status', 'both'):
                            statuses.append({'context': 'other', 'state': 'error'})
                        if reverse:
                            runs.reverse()
                        diagnostics = {}
                        values = [json.dumps({'head': {'sha': SHA}}),
                                  json.dumps({'check_runs': runs}),
                                  json.dumps({'statuses': statuses})]
                        with patch('kodi.github.gh', side_effect=values):
                            self.assertFalse(checks_pass(URL, SHA, ['system-tests'],
                                                         diagnostics=diagnostics))
                        self.assertEqual(diagnostics, {'terminal': True, 'failure_signal': signal})

    def test_competing_or_stale_specific_ci_evidence_stays_unknown(self):
        for runs in (
                [{**RUN, 'conclusion': 'startup_failure'},
                 {**RUN, 'conclusion': 'timed_out'}],
                [{**RUN, 'conclusion': 'failure'},
                 {**RUN, 'conclusion': 'startup_failure', 'head_sha': 'b'*40}],
                [{**RUN, 'conclusion': 'startup_failure'},
                 {**RUN, 'conclusion': 'timed_out', 'head_sha': 'b'*40}]):
            diagnostics = {}
            values = [json.dumps({'head': {'sha': SHA}}),
                      json.dumps({'check_runs': runs}), json.dumps({'statuses': [STATUS]})]
            with patch('kodi.github.gh', side_effect=values):
                self.assertFalse(checks_pass(URL, SHA, ['system-tests'], diagnostics=diagnostics))
            self.assertEqual(diagnostics, {'terminal': True})

    def test_reused_diagnostics_cannot_retain_previous_candidate_cause(self):
        diagnostics = {'terminal': True, 'failure_signal': 'ci_disabled'}
        for head, runs, expected in [
                ('b'*40, [RUN], {'terminal': False}),
                (SHA, [{**RUN, 'conclusion': 'failure'}], {'terminal': True}),
                (SHA, [RUN], {'terminal': False})]:
            diagnostics.update(terminal=True, failure_signal='ci_disabled')
            with patch('kodi.github.gh', side_effect=[
                    json.dumps({'head': {'sha': head}}),
                    json.dumps({'check_runs': runs}), json.dumps({'statuses': [STATUS]})]):
                checks_pass(URL, SHA, ['system-tests'], diagnostics=diagnostics)
            self.assertEqual(diagnostics, expected)
