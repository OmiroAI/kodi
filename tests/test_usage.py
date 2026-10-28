import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from kodi.cli import main, usage_summary
from kodi.store import Store
from kodi.coordinator import Coordinator


class UsageFixture:
    def setUp(self):
        self.store=Store(':memory:')
        self.addCleanup(self.store.db.close)

    def rows(self):
        return self.store.db.execute('SELECT job,provider,model,data,seconds FROM usage ORDER BY id')

    def invoke(self, *arguments):
        output=io.StringIO()
        with patch('sys.argv',['kodi','usage',*arguments]), \
                patch('kodi.cli.Path.read_text',return_value='{}'), \
                patch('kodi.cli.Coordinator',return_value=Mock(store=self.store)), \
                contextlib.redirect_stdout(output):
            main()
        return output.getvalue()


class UsageTests(UsageFixture, unittest.TestCase):
    def test_groups_runs_across_jobs_and_models(self):
        self.store.usage(1,'codex','one',{'input_tokens':10,'output_tokens':4},1.5)
        self.store.usage(2,'codex','two',{'input_tokens':20,'output_tokens':6},2.5)
        self.store.usage(2,'qwen','three',{'prompt_tokens':7,'completion_tokens':3},2)
        self.assertEqual(usage_summary(self.rows()), {
            'codex':{'run_count':2,'seconds':4,'input_tokens':30,'output_tokens':10,'total_tokens':40},
            'qwen':{'run_count':1,'seconds':2,'input_tokens':7,'output_tokens':3,'total_tokens':10}})

    def test_unknown_counts_make_provider_totals_unknown(self):
        for unknown_first in (True,False):
            with self.subTest(unknown_first=unknown_first):
                rows=[{'provider':'codex','seconds':1,'data':json.dumps(data)}
                      for data in ({},{'input_tokens':8,'output_tokens':2})]
                result=usage_summary(rows if unknown_first else rows[::-1])['codex']
                self.assertEqual(result, {'run_count':2,'seconds':2,'input_tokens':None,
                                          'output_tokens':None,'total_tokens':None})

    def test_partial_unknown_and_known_zero(self):
        self.store.usage(1,'qwen','local',{'prompt_tokens':0},1)
        result=usage_summary(self.rows())['qwen']
        self.assertEqual(result['input_tokens'],0)
        self.assertIsNone(result['output_tokens'])
        self.assertIsNone(result['total_tokens'])

    def test_invalid_counts_are_unknown(self):
        for value in (None,True,-1,'12',1.5):
            with self.subTest(value=value):
                rows=[{'provider':'codex','seconds':1,
                       'data':json.dumps({'input_tokens':value,'output_tokens':0})}]
                self.assertIsNone(usage_summary(rows)['codex']['input_tokens'])

    def test_default_output_is_unchanged_and_summary_is_optional(self):
        self.store.usage(1,'codex','default',{'input_tokens':0,'output_tokens':0},3)
        expected=''.join(json.dumps(dict(row))+'\n' for row in self.rows())
        self.assertEqual(self.invoke(),expected)
        self.assertEqual(json.loads(self.invoke('--summary')),usage_summary(self.rows()))

    def test_empty_summary(self):
        self.assertEqual(self.invoke('--summary'),'{}\n')
        self.assertEqual(self.invoke(),'')


class UsageReportTests(UsageFixture, unittest.TestCase):
    def test_daily_uses_utc_and_evidence_dates(self):
        job=self.store.enqueue('task','example','Example','requirements')
        with patch('kodi.store.now',return_value='2026-10-24T23:30:00-02:00'):
            self.store.usage(job['id'],'codex','default',{'input_tokens':10,'output_tokens':2},3)
            self.store.evidence(job['id'],'abc','review',{'approved':False})
        with patch('kodi.store.now',return_value='2026-10-26T01:00:00+00:00'):
            self.store.evidence(job['id'],'def','review',{'approved':True})
            self.store.evidence(job['id'],'def','qa',{'passed':True})
        report=json.loads(self.invoke('--daily'))
        self.assertEqual(list(report),['2026-10-25','2026-10-26'])
        self.assertEqual(report['2026-10-25']['providers']['codex']['total_tokens'],12)
        self.assertEqual(report['2026-10-25']['review_iterations'],1)
        self.assertEqual(report['2026-10-26'],{'providers':{},'review_iterations':1,'qa_runs':1})

    def test_task_identity_zero_usage_and_repeated_reviews(self):
        job=self.store.enqueue('task','example','Example','requirements',board_id='board-task')
        empty=self.store.enqueue('empty','example','Empty','requirements')
        self.store.update(job['id'],attempt=3)
        self.store.usage(job['id'],'codex','default',{},2)
        for approved in (False,True):
            self.store.evidence(job['id'],'same-sha','review',{'approved':approved})
        report=json.loads(self.invoke('--tasks'))
        task=report[str(job['id'])]
        self.assertEqual(task['board_id'],'board-task')
        self.assertEqual(task['coding_attempts'],3)
        self.assertEqual(task['review_iterations'],2)
        self.assertIsNone(task['providers']['codex']['total_tokens'])
        self.assertEqual(report[str(empty['id'])]['providers'],{})
        self.assertEqual(report[str(empty['id'])]['review_iterations'],0)

    def test_board_progress_matches_task_counts_and_totals(self):
        job=self.store.enqueue('task','example','Example','requirements',board_id='board-task')
        self.store.update(job['id'],attempt=2)
        self.store.usage(job['id'],'codex','default',{'input_tokens':10,'output_tokens':2},3)
        self.store.usage(job['id'],'codex','default',{'input_tokens':20,'output_tokens':3},4)
        self.store.evidence(job['id'],'abc','review',{'approved':False})
        self.store.evidence(job['id'],'def','review',{'approved':True})
        self.store.evidence(job['id'],'def','qa',{'passed':True})
        self.store.evidence(job['id'],'','coding_profile',
                            {'profile':'terra','model':'gpt-6.1-terra','effort':'high'})
        self.store.evidence(job['id'],'','recovery_profile',
                            {'profile':'sol','model':'gpt-6.1-sol','effort':'medium'})
        coordinator=Coordinator.__new__(Coordinator)
        coordinator.store=self.store
        coordinator.board=Mock()
        coordinator.progress(job)
        task_id, description=coordinator.board.description.call_args.args
        self.assertEqual(task_id,'board-task')
        self.assertIn('codex: 35 tokens; 2 runs; 7s',description)
        self.assertIn('Coding attempts: 2; review iterations: 2; QA runs: 1',description)
        self.assertIn('Author profile: terra; model: gpt-6.1-terra; effort: high',description)
        self.assertIn('Author profile: sol; model: gpt-6.1-sol; effort: medium',description)
        self.assertTrue(description.startswith('requirements'))
