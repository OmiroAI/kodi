import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator
from kodi.runner import QuotaExhausted


class ReviewBudgetTests(unittest.TestCase):
    def coordinator(self, **config):
        c=Coordinator.__new__(Coordinator)
        c.config=config
        c.store=Mock()
        c.store.qa_summary.return_value={"outcome":"missing"}
        c.store.review_context.return_value={"findings": []}
        return c

    def invoke(self, coordinator, requirements):
        with patch('kodi.coordinator.git', return_value=''), patch(
                'kodi.coordinator.review_material', return_value=('', '', [])):
            return coordinator.review({'id':1, 'requirements':requirements},Path('/unused'),'candidate',{})

    def test_default_rejects_large_context_before_any_provider(self):
        c=self.coordinator()
        c.review_provider=Mock()
        with self.assertRaisesRegex(RuntimeError, r'> 200000 characters'):
            self.invoke(c,'x'*210000)
        c.review_provider.assert_not_called()

    def test_override_preserves_prompt_and_candidate_through_fallback(self):
        c=self.coordinator(review_context_max_chars=250000,reviewer='antigravity',
                           qwen_url='https://example.invalid',qwen_model='example',qwen_env='private.env')
        c.review_provider=Mock(side_effect=[QuotaExhausted(),{'approved':True}])
        requirements='x'*210000
        self.assertTrue(self.invoke(c,requirements)['approved'])
        first,second=c.review_provider.call_args_list
        self.assertEqual(first.args[:-1],second.args[:-1])
        self.assertIn(requirements,first.args[3])
        self.assertEqual(first.args[2],'candidate')
        self.assertEqual((first.args[-1],second.args[-1]),('antigravity','qwen'))

    def test_override_still_enforces_budget(self):
        c=self.coordinator(review_context_max_chars=250000)
        c.review_provider=Mock()
        with self.assertRaisesRegex(RuntimeError, r'> 250000 characters'):
            self.invoke(c,'x'*250000)
        c.review_provider.assert_not_called()

    def test_invalid_limits_fail_before_review(self):
        for value in (True,False,0,-1,1000001,250000.0,'250000',None):
            c=self.coordinator(review_context_max_chars=value)
            c.review_provider=Mock()
            with self.subTest(value=value), patch('kodi.coordinator.git') as git, patch(
                    'kodi.coordinator.review_material') as material:
                with self.assertRaises(ValueError):
                    c.review({'requirements':'small'},Path('/unused'),'candidate',{})
                git.assert_not_called()
                material.assert_not_called()
            c.review_provider.assert_not_called()

    def test_raising_context_limit_does_not_raise_diff_limit(self):
        c=self.coordinator(review_context_max_chars=250000)
        c.review_provider=Mock()
        # Exercise the byte-safe material builder's actual diff budget, rather
        # than mocking the removed text-only diff call in coordinator.review.
        with patch('kodi.coordinator.git',return_value='base'), patch(
                'kodi.review_diff.subprocess.check_output',side_effect=[
                    b'source.py\0', b'',
                    b'100644 blob '+b'a'*40+b'\tsource.py\0',
                    b'x'*100001, b'+'+b'x'*100001]):
            with self.assertRaisesRegex(RuntimeError,'Change exceeds review budget'):
                c.review({'requirements':''},Path('/unused'),'candidate',{})
        c.review_provider.assert_not_called()
