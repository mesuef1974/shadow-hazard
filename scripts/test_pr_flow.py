import unittest

import pr_flow


class PrFlowTests(unittest.TestCase):
    def test_base_defaults_to_main_and_reads_trailer(self):
        self.assertEqual(pr_flow.base_from_message('x'), 'main')
        self.assertEqual(pr_flow.base_from_message('x\n\nPR-Base: docs/a\n'), 'docs/a')

    def test_main_is_never_merged_here(self):
        self.assertEqual(pr_flow.decide('main', None), 'skip')
        self.assertEqual(pr_flow.decide('f', {'baseRefName': 'main'}), 'human')

    def test_open_then_merge_non_main(self):
        self.assertEqual(pr_flow.decide('f', None), 'open')
        self.assertEqual(pr_flow.decide('f', {'baseRefName': 'docs/a'}), 'merge')

    def test_draft_is_left_alone(self):
        self.assertEqual(pr_flow.decide('f', {'baseRefName': 'docs/a', 'isDraft': True}), 'skip')


if __name__ == '__main__':
    unittest.main()
