import unittest
from types import SimpleNamespace

import pr_flow


class PrFlowTests(unittest.TestCase):
    def test_base_defaults_to_main_and_reads_trailer(self):
        self.assertEqual(pr_flow.base_from_message('x'), 'main')
        self.assertEqual(pr_flow.base_from_message('x\n\nPR-Base: docs/a\n'), 'docs/a')

    def test_push_to_main_skips_and_green_pr_to_main_merges(self):
        self.assertEqual(pr_flow.decide('main', None), 'skip')
        self.assertEqual(pr_flow.decide('f', {'baseRefName': 'main'}), 'merge')

    def test_open_then_merge_non_main(self):
        self.assertEqual(pr_flow.decide('f', None), 'open')
        self.assertEqual(pr_flow.decide('f', {'baseRefName': 'docs/a'}), 'merge')

    def test_behind_branch_is_updated_before_merge(self):
        self.assertEqual(pr_flow.decide('f', {'baseRefName': 'main', 'mergeStateStatus': 'BEHIND'}), 'update')

    def test_merge_waits_for_second_ci_run_only(self):
        blocked = SimpleNamespace(returncode=1, stderr='the base branch policy prohibits the merge')
        done = SimpleNamespace(returncode=0, stderr='')
        replies, waits = [blocked, blocked, done], []
        self.assertIs(pr_flow.merge_when_allowed(lambda: replies.pop(0), waits.append), done)
        self.assertEqual(len(waits), 2)
        other = SimpleNamespace(returncode=1, stderr='conflict')
        self.assertIs(pr_flow.merge_when_allowed(lambda: other, waits.append), other)
        self.assertEqual(len(waits), 2)

    def test_draft_is_left_alone(self):
        self.assertEqual(pr_flow.decide('f', {'baseRefName': 'docs/a', 'isDraft': True}), 'skip')


if __name__ == '__main__':
    unittest.main()
