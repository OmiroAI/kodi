import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator
from kodi.runner import QuotaExhausted, quota_exhausted, run_worker

VERDICT = dict(approved=True, criteria_met=True, simple=True,
               blocking_findings=[], summary='Reviewed')


class ReviewerFallbackTests(unittest.TestCase):
    def test_exhaustion_is_distinct_from_rate_limits_and_auth(self):
        for message in ('Individual quota reached', 'insufficient_quota',
                        'Insufficient credits', 'exceeded your current quota'):
            self.assertTrue(quota_exhausted(message))
        for message in ('429 RESOURCE_EXHAUSTED', 'rate limit', '401 unauthorized',
                        'billing error', 'service unavailable'):
            self.assertFalse(quota_exhausted(message))

    def test_runner_detects_quota_with_or_without_envelope(self):
        for envelope in (None, dict(status='ERROR', structured_output=VERDICT)):
            with self.subTest(envelope=envelope), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                checkout = root/'checkout'; checkout.mkdir(); (checkout/'.git').mkdir()
                result = subprocess.CompletedProcess([], 1,
                    '' if envelope is None else json.dumps(dict(event='result', result=envelope)),
                    'Individual quota reached')
                usage = Mock()
                with patch('kodi.runner.subprocess.run', return_value=result):
                    with self.assertRaises(QuotaExhausted):
                        run_worker(dict(state_dir=directory, proxy_dir=directory,
                                        worker_image='worker'), checkout, root,
                                   'Review', 'review', 'antigravity', usage)
                usage.assert_called_once()
                self.assertTrue((root/'review-output'/'stderr.log').is_file())

    def coordinator(self):
        coordinator = Coordinator.__new__(Coordinator)
        coordinator.config = dict(reviewer='antigravity', qwen_url='https://example.invalid/v1',
                                  qwen_model='qwen-example', qwen_env='/private/qwen.env')
        coordinator.store = Mock()
        return coordinator

    def test_fallback_once_preserves_commit_and_prompt(self):
        coordinator = self.coordinator()
        with patch('kodi.coordinator.git', return_value=''), patch(
                'kodi.coordinator.review_material', return_value=('', '', [])), patch.object(
                coordinator, 'review_provider', side_effect=[QuotaExhausted(), VERDICT]) as review:
            result = coordinator.review(dict(id=7, requirements='Requirement'), Path('/unused'),
                                        'candidate', dict(test_steps=['Verify']))
        self.assertEqual(result, VERDICT)
        self.assertEqual([call.args[-1] for call in review.call_args_list], ['antigravity', 'qwen'])
        self.assertEqual(review.call_args_list[0].args[:-1], review.call_args_list[1].args[:-1])

    def test_other_errors_and_rejections_never_trigger_fallback(self):
        coordinator = self.coordinator()
        for error in (RuntimeError('Auth'), RuntimeError('Malformed verdict')):
            with patch('kodi.coordinator.git', return_value=''), patch(
                'kodi.coordinator.review_material', return_value=('', '', [])), patch.object(
                    coordinator, 'review_provider', side_effect=error) as review:
                with self.assertRaises(RuntimeError):
                    coordinator.review(dict(requirements=''), Path('/unused'), 'sha', {})
                review.assert_called_once()
        with patch('kodi.coordinator.git', return_value=''), patch(
                'kodi.coordinator.review_material', return_value=('', '', [])), patch.object(
                coordinator, 'review_provider', return_value=dict(VERDICT, approved=False)) as review:
            self.assertFalse(coordinator.review(dict(requirements=''), Path('/unused'), 'sha', {})['approved'])
            review.assert_called_once()

    def test_disabled_missing_or_exhausted_fallback_blocks(self):
        for config in (dict(reviewer_fallback=None), dict(qwen_env=None), dict(reviewer='qwen')):
            coordinator = self.coordinator(); coordinator.config.update(config)
            with patch('kodi.coordinator.git', return_value=''), patch(
                'kodi.coordinator.review_material', return_value=('', '', [])), patch.object(
                    coordinator, 'review_provider', side_effect=QuotaExhausted()) as review:
                with self.assertRaises(QuotaExhausted):
                    coordinator.review(dict(requirements=''), Path('/unused'), 'sha', {})
                review.assert_called_once()
        coordinator = self.coordinator()
        with patch('kodi.coordinator.git', return_value=''), patch(
                'kodi.coordinator.review_material', return_value=('', '', [])), patch.object(
                coordinator, 'review_provider', side_effect=QuotaExhausted()) as review:
            with self.assertRaises(QuotaExhausted):
                coordinator.review(dict(requirements=''), Path('/unused'), 'sha', {})
            self.assertEqual(review.call_count, 2)

    def test_qwen_evidence_names_actual_reviewer_and_commit(self):
        coordinator = self.coordinator()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)/'checkout'; checkout.mkdir()
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps(dict(choices=[dict(message=dict(
                content=json.dumps(VERDICT)))], usage=dict(prompt_tokens=10))).encode()
            with patch('kodi.coordinator.read_env', return_value=dict(QWEN_API_KEY='test')), patch(
                    'kodi.coordinator.urlopen', return_value=response):
                review = coordinator.review_provider(dict(id=7), checkout, 'sha', 'Full diff', 'qwen')
            self.assertEqual((review['provider'], review['model'], review['reviewer_family']),
                             ('qwen', 'qwen-example', 'qwen'))
            self.assertEqual(coordinator.store.evidence.call_args.args[1], 'sha')
            coordinator.store.usage.assert_called_once()
            with self.assertRaises(ValueError):
                coordinator.review_provider(dict(id=7), checkout, 'sha', 'Diff', 'codex')
