import tempfile
import unittest
from pathlib import Path

from kodi.store import Store
from kodi.policy import validate_review
from kodi.reviewer_admission import request, AdmissionBusy


class ReliabilityTests(unittest.TestCase):
    def test_admission_survives_restart_and_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.db'
            first, second = Store(path), Store(path)
            job = first.enqueue('example:1', 'project', 'Review', 'Requirements')['id']
            with request(first, job, 'candidate', 'qwen'):
                with self.assertRaises(AdmissionBusy):
                    with request(second, job, 'candidate', 'qwen'):
                        self.fail('concurrent request admitted')
            with request(second, job, 'candidate', 'qwen'):
                pass
            with self.assertRaises(KeyboardInterrupt):
                with request(first, job, 'candidate', 'qwen'):
                    raise KeyboardInterrupt()
            restarted = Store(path)
            with self.assertRaises(AdmissionBusy):
                with request(restarted, job, 'candidate', 'qwen'):
                    self.fail('uncertain request replaced')
            for store in (first, second, restarted):
                store.db.close()

    def test_consistency(self):
        valid = dict(approved=True, criteria_met=True, simple=True,
                     blocking_findings=[], summary='Reviewed source and tests')
        self.assertTrue(validate_review(valid, 'openai', 'qwen')['approved'])
        for change in (dict(approved='true'), dict(criteria_met=False),
                       dict(simple=False), dict(delivery_met=False),
                       dict(summary='Not approved'), dict(summary=''),
                       dict(blocking_findings=['Concrete defect; retracting it'])):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_review({**valid, **change}, 'openai', 'qwen')
        with self.assertRaises(ValueError):
            validate_review(valid, 'openai', 'openai')
