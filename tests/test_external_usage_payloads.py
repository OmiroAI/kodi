import json
import unittest

from kodi.activity_payloads import InvalidActivity, evidence_event, usage_event

INSTANCE='d2d93284-5aab-4384-b50c-ac5bebc8e133'


def run_row(provider='codex', data=None, **fields):
    return {'id':42,'job':3,'provider':provider,'model':'reported-model',
            'data':json.dumps(data if data is not None else {}),'seconds':1.25,
            'created':'2026-01-02T00:30:00+02:00',**fields}


class ExternalUsagePayloadTests(unittest.TestCase):
    def test_codex_inclusive_counts_keep_subsets_separate_and_allowlist_data(self):
        payload=usage_event(run_row(data={'input_tokens':100,'output_tokens':20,
            'cached_input_tokens':40,'reasoning_output_tokens':5,'transcript':'do-not-send'},
            phase='coding',outcome='succeeded'),INSTANCE)
        self.assertEqual(payload.get('input_tokens'),100)
        self.assertEqual(payload.get('output_tokens'),20)
        self.assertEqual(payload.get('cached_input_tokens'),40)
        self.assertEqual(payload.get('reasoning_output_tokens'),5)
        self.assertEqual(payload.get('duration_ms'),1250)
        self.assertEqual(payload.get('phase'),'coding')
        self.assertEqual(payload.get('outcome'),'succeeded')
        self.assertEqual(payload.get('event_id'),'usage:42')
        self.assertEqual(payload.get('occurred_at'),'2026-01-01T22:30:00+00:00')
        self.assertNotIn('do-not-send',json.dumps(payload))

    def test_qwen_compatible_usage_keeps_cache_and_reasoning_in_parent_totals(self):
        payload=usage_event(run_row('qwen',{'prompt_tokens':100,'completion_tokens':20,
            'prompt_tokens_details':{'cached_tokens':40},
            'completion_tokens_details':{'reasoning_tokens':5}}),INSTANCE)
        self.assertEqual((payload.get('input_tokens'),payload.get('output_tokens')),(100,20))
        self.assertEqual((payload.get('cached_input_tokens'),payload.get('reasoning_output_tokens')),(40,5))

    def test_unknown_zero_invalid_and_unsupported_measurements_remain_distinct(self):
        payload=usage_event(run_row(data={'input_tokens':0,'output_tokens':True,
            'cached_input_tokens':2},model='provider default'),INSTANCE)
        self.assertEqual(payload.get('input_tokens'),0)
        self.assertIsNone(payload.get('output_tokens'))
        self.assertIsNone(payload.get('cached_input_tokens'))
        self.assertNotIn('model',payload)
        self.assertEqual((payload.get('phase'),payload.get('outcome')),('unknown','unknown'))
        unknown=usage_event(run_row('unsupported',{'input_tokens':10,'output_tokens':20}),INSTANCE)
        self.assertIsNone(unknown.get('input_tokens'))
        self.assertIsNone(unknown.get('output_tokens'))

    def test_bad_timestamps_and_overflow_require_reconciliation(self):
        for row in [run_row(created='not-a-time'),run_row(created='2026-01-01'),
                    run_row(data={'input_tokens':2**63-1,'output_tokens':1})]:
            with self.subTest(row=row),self.assertRaises(InvalidActivity): usage_event(row,INSTANCE)

    def test_failed_runs_preserve_reported_counts_and_explicit_metadata(self):
        payload=usage_event(run_row(data={'input_tokens':4,'output_tokens':0},
            phase='recovery',outcome='timeout'),INSTANCE)
        self.assertEqual((payload.get('phase'),payload.get('outcome')),('recovery','timeout'))
        self.assertEqual((payload.get('input_tokens'),payload.get('output_tokens')),(4,0))

    def test_distinct_review_and_qa_results_do_not_reuse_sha_as_event_identity(self):
        rows=[{'id':7,'kind':'review','data':json.dumps({'approved':False})},
              {'id':8,'kind':'review','data':json.dumps({'approved':True})},
              {'id':9,'kind':'qa','data':json.dumps({'passed':False})}]
        payloads=[evidence_event({**row,'sha':'a'*40,'created':'2026-01-01T00:00:00Z'},INSTANCE) for row in rows]
        self.assertEqual([p.get('event_id') if p else None for p in payloads],['evidence:7','evidence:8','evidence:9'])
        self.assertEqual([p['outcome'] for p in payloads],['rejected','approved','failed'])
        self.assertTrue(all('phase' not in p and 'input_tokens' not in p for p in payloads))
        self.assertIsNone(evidence_event({'kind':'author'},INSTANCE))

    def test_missing_verdict_is_not_reported_as_a_failed_review(self):
        with self.assertRaises(InvalidActivity):
            evidence_event({'id':1,'kind':'review','data':'{}','sha':'a'*40,
                            'created':'2026-01-01T00:00:00Z'},INSTANCE)


if __name__=='__main__': unittest.main()
