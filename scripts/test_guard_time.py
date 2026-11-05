import unittest

import guard_time

DIFF = '''+++ b/backend/token_usage/tests.py
+        now=timezone.now()
+        now=timezone.now().replace(minute=30,second=0)
+        stamp=time.time()  # زمن-ثابت
+++ b/backend/token_usage/analytics.py
+        now=timezone.now()
'''


class GuardTimeTests(unittest.TestCase):
    def test_flags_only_unpinned_clock_in_test_files(self):
        found = guard_time.scan_diff(DIFF)
        self.assertEqual(found, [('backend/token_usage/tests.py', 'now=timezone.now()')])

    def test_ignores_removed_lines_and_non_test_files(self):
        self.assertEqual(guard_time.scan_diff('+++ b/a/tests.py\n-        now=timezone.now()\n'), [])


if __name__ == '__main__':
    unittest.main()
