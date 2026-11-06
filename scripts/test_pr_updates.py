import json
import unittest
from types import SimpleNamespace
from unittest import mock

import pr_updates


def pr(number, title='bump x from 1.2.3 to 1.2.4', branch='dependabot/pip/backend/x-1.2.4',
       author=pr_updates.BOT, state='CLEAN', draft=False):
    return {'number': number, 'title': title, 'headRefName': branch, 'isDraft': draft,
            'author': {'login': author}, 'mergeStateStatus': state}


def reply(code=0, stdout='', stderr=''):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class PrUpdatesTests(unittest.TestCase):
    def test_patch_and_minor_are_small(self):
        self.assertTrue(pr_updates.small(pr(1)))
        title = 'bump playwright from 1.58.0 to 1.64.1 in /tools/ci'
        self.assertTrue(pr_updates.small(pr(1, title)))

    def test_pip_major_waits_for_the_owner(self):
        self.assertFalse(pr_updates.small(pr(1, 'bump django from 5.2.18 to 6.1.2 in /backend')))

    def test_unreadable_title_is_not_small(self):
        self.assertFalse(pr_updates.small(pr(1, 'bump several packages')))

    def test_actions_major_is_tested_by_ci_itself(self):
        branch = 'dependabot/github_actions/actions/checkout-7.0.1'
        title = 'bump actions/checkout from 6.1.0 to 7.0.1'
        self.assertTrue(pr_updates.small(pr(1, title, branch)))

    def test_picks_the_oldest_ready_one_only(self):
        self.assertEqual(pr_updates.pick([pr(28), pr(21), pr(25)]), 21)

    def test_never_picks_what_is_not_ready(self):
        prs = [pr(1, state='BEHIND'), pr(2, state='BLOCKED'), pr(3, draft=True),
               pr(4, author='mesuef1974')]
        self.assertIsNone(pr_updates.pick(prs))

    def run_merge(self, prs, merged=None):
        calls = []

        def run(*args):
            calls.append(args[:3])
            return reply(stdout=json.dumps(prs)) if args[1] == 'list' else (merged or reply())

        with mock.patch.object(pr_updates, 'record') as record:
            pr_updates.merge_small_update('.', run)
        return calls, record

    def test_merges_one_and_records_it(self):
        calls, record = self.run_merge([pr(27), pr(28)])
        self.assertEqual(calls[1], ('pr', 'merge', '27'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(record.call_args.args[:3], ('pr-update', 'pass', 'دمج ترقية الطلب 27'))

    def test_nothing_ready_records_nothing(self):
        calls, record = self.run_merge([])
        self.assertEqual(len(calls), 1)
        record.assert_not_called()

    def test_refused_merge_is_recorded_not_raised(self):
        _, record = self.run_merge([pr(27)], reply(1, stderr='conflict'))
        self.assertEqual(record.call_args.args[1], 'fail')
        self.assertIn('conflict', record.call_args.args[2])

    def test_broken_listing_is_recorded_not_raised(self):
        with mock.patch.object(pr_updates, 'record') as record:
            pr_updates.merge_small_update('.', lambda *a: reply(1, stderr='boom'))
        self.assertIn('boom', record.call_args.args[2])


if __name__ == '__main__':
    unittest.main()
