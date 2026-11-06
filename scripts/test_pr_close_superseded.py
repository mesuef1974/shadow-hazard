import unittest

import pr_close_superseded as c

MERGED = [{'number': 7, 'headRefOid': 'tip'}]


class SupersededTests(unittest.TestCase):
    def test_ancestor_of_merged_head_is_superseded(self):
        self.assertEqual(c.superseded_by({'headRefOid': 'old'}, MERGED, lambda a, b: True), 7)

    def test_unique_work_stays_open(self):
        self.assertIsNone(c.superseded_by({'headRefOid': 'old'}, MERGED, lambda a, b: False))

    def test_draft_stays_open(self):
        self.assertIsNone(c.superseded_by({'headRefOid': 'old', 'isDraft': True}, MERGED, lambda a, b: True))

    def test_same_head_is_not_its_own_winner(self):
        self.assertIsNone(c.superseded_by({'headRefOid': 'tip'}, MERGED, lambda a, b: True))


if __name__ == '__main__':
    unittest.main()
