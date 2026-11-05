import unittest

import pr_status


def pr(**kw):
    base = {'number': 1, 'title': 't', 'baseRefName': 'main', 'headRefName': 'f',
            'reviewDecision': '', 'mergeStateStatus': 'CLEAN', 'statusCheckRollup': [], 'isDraft': False}
    return {**base, **kw}


class PrStatusTests(unittest.TestCase):
    def test_conflict_wins_over_review(self):
        self.assertEqual(pr_status.classify(pr(mergeStateStatus='DIRTY', reviewDecision='REVIEW_REQUIRED'))[0], 'تعارض')

    def test_failed_check_is_named(self):
        state, action = pr_status.classify(pr(statusCheckRollup=[{'name': 'Backend', 'conclusion': 'FAILURE'}]))
        self.assertEqual(state, 'فحوص فاشلة')
        self.assertIn('Backend', action)

    def test_review_required_then_ready(self):
        self.assertEqual(pr_status.classify(pr(reviewDecision='REVIEW_REQUIRED'))[0], 'ينتظر مراجعًا')
        self.assertEqual(pr_status.classify(pr())[0], 'جاهز للدمج')

    def test_report_is_a_table_and_handles_empty(self):
        self.assertIn('| #1 t |', pr_status.report([pr()]))
        self.assertIn('لا طلبات مفتوحة', pr_status.report([]))


if __name__ == '__main__':
    unittest.main()
