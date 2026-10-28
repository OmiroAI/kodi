import unittest

from scripts.qa_review_pilot import METRICS, compare


class PilotTests(unittest.TestCase):
    def run_record(self, arm, value):
        return dict(candidate_sha='fixture', repeat=1, arm=arm,
                    **{m: value for m in METRICS})

    def test_measured_pairs_and_finding_quality(self):
        result = compare([self.run_record('before', 2), self.run_record('after', 1)])
        self.assertEqual(result['pairs'], 1)
        self.assertEqual(result['after_minus_before']['broad_reruns'], -1)
        self.assertEqual(result['after_minus_before']['missed_defects'], -1)

    def test_incomplete_duplicate_and_unknown_measurements_rejected(self):
        before = self.run_record('before', 1)
        after = self.run_record('after', 1)
        for runs in ([], [before], [before, before, after],
                     [before, dict(after, valid_findings=None)]):
            with self.subTest(runs=runs), self.assertRaises(ValueError):
                compare(runs)
