import unittest

import failure_digest

SAMPLE = """Found 2 test(s).
ERROR: test_a (workspace.test_x.PlatformWorkerTests.test_a)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "x.py", line 1, in f
    raise e
psycopg.OperationalError: the connection is closed

ERROR: test_b (workspace.test_x.PlatformWorkerTests.test_b)
----------------------------------------------------------------------
Traceback (most recent call last):
django.db.utils.OperationalError: the connection is closed

----------------------------------------------------------------------
Ran 67 tests in 38.366s

FAILED (errors=2, skipped=4)
"""


class FailureDigestTests(unittest.TestCase):
    def test_digest_lists_failures_with_first_error_and_summary(self):
        out = failure_digest.build(SAMPLE, 'Backend / postgres', ['backend/a.py'])
        self.assertIn('ERROR workspace.test_x.PlatformWorkerTests.test_a', out)
        self.assertIn('psycopg.OperationalError: the connection is closed', out)
        self.assertIn('FAILED (errors=2, skipped=4)', out)
        self.assertIn('backend/a.py', out)

    def test_digest_is_bounded(self):
        many = '\n'.join(f'ERROR: t{i} (m.C.t{i})\nValueError: boom {i}' for i in range(200))
        out = failure_digest.build(many, 'x', [f'f{i}' for i in range(100)])
        self.assertLessEqual(len(out.splitlines()), failure_digest.MAX_LINES)

    def test_no_failures_still_points_at_log_tail(self):
        out = failure_digest.build('step failed\nsomething broke\n', 'x')
        self.assertIn('something broke', out)


if __name__ == '__main__':
    unittest.main()


class RerunVerdictTests(unittest.TestCase):
    LOG = 'FAIL: test_a (app.tests.T.test_a)\nAssertionError: 1 != 2\n\nRan 1 test in 0.1s\n\nFAILED (failures=1)\n'

    def test_failed_ids_are_dotted_and_unique(self):
        self.assertEqual(failure_digest.failed_ids(self.LOG + self.LOG), ['app.tests.T.test_a'])
        self.assertEqual(len(failure_digest.failed_ids(self.LOG + self.LOG)), 1)

    def test_verdict_from_rerun_log(self):
        self.assertEqual(failure_digest.verdict('Ran 1 test in 0.1s\n\nOK\n'), 'flaky')
        self.assertEqual(failure_digest.verdict(self.LOG), 'real')
        self.assertIsNone(failure_digest.verdict(''))

    def test_digest_carries_verdict_first(self):
        text = failure_digest.build(self.LOG, 'x', [], 'flaky')
        self.assertIn('متقطع', text.splitlines()[2])
