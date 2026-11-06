import json
import unittest
from types import SimpleNamespace
from unittest import mock

import pr_refresh


def reply(code=0, stdout='', stderr=''):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class PrRefreshTests(unittest.TestCase):
    def test_only_behind_non_draft_is_chosen(self):
        prs = [{'number': 1, 'mergeStateStatus': 'BEHIND'},
               {'number': 2, 'mergeStateStatus': 'BEHIND', 'isDraft': True},
               {'number': 3, 'mergeStateStatus': 'BLOCKED'}]
        self.assertEqual(pr_refresh.behind(prs), [1])

    def test_updates_behind_pr_and_records_result(self):
        calls = []

        def run(*args):
            calls.append(args[:3])
            return reply(stdout=json.dumps([{'number': 18, 'mergeStateStatus': 'BEHIND'}]))

        with mock.patch.object(pr_refresh, 'record') as record:
            pr_refresh.refresh('.', run)
        self.assertEqual(calls[1], ('pr', 'update-branch', '18'))
        self.assertEqual(record.call_args.args[:2], ('pr-update', 'pass'))
        self.assertEqual(record.call_args.args[2], 'تحديث الطلب 18')

    def test_failure_never_stops_deploy(self):
        def broken(*args):
            raise OSError('gh غائب')

        for run in (broken, lambda *a: reply(1, stderr='x'), lambda *a: reply(stdout='not json')):
            with mock.patch.object(pr_refresh, 'record') as record:
                pr_refresh.refresh('.', run)
            self.assertEqual(record.call_args.args[:2][1], 'fail')


if __name__ == '__main__':
    unittest.main()
