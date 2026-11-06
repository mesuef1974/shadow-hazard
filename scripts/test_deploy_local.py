import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deploy_local

NEW, OLD = 'new1234567', 'old1234567'


class DeployLocalTests(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(deploy_local.plan('a', 'a', False), 'idle')
        self.assertEqual(deploy_local.plan('a', 'b', False), 'deploy')
        self.assertEqual(deploy_local.plan('a', 'b', True), 'refuse')

    def test_failed_commit_is_not_retried_but_a_newer_one_is(self):
        self.assertEqual(deploy_local.plan('a', 'b', False, 'b'), 'idle')
        self.assertEqual(deploy_local.plan('a', 'c', False, 'b'), 'deploy')

    def attempt(self, run_ok=True, healthy=True):
        moves = []
        with patch.object(deploy_local, 'steps', return_value=[('الهجرة', ['x'])]):
            result = deploy_local.deploy(Path('.'), NEW, moves.append, lambda r, c: run_ok,
                                         lambda r: healthy, OLD)
        return result, moves

    def test_success_stays_on_target(self):
        (ok, _), moves = self.attempt()
        self.assertTrue(ok)
        self.assertEqual(moves, [NEW])

    def test_failed_step_rolls_back(self):
        (ok, reason), moves = self.attempt(run_ok=False)
        self.assertFalse(ok)
        self.assertEqual(moves[-1], OLD)
        self.assertIn('الهجرة', reason)

    def test_unhealthy_platform_rolls_back(self):
        (ok, _), moves = self.attempt(healthy=False)
        self.assertFalse(ok)
        self.assertEqual(moves[-1], OLD)

    def worker_problem(self, reported, age):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / deploy_local.FAILED
            marker.parent.mkdir()
            marker.write_text('', encoding='utf-8')
            if reported:
                version = root / deploy_local.WORKER_VERSION
                version.parent.mkdir(parents=True)
                version.write_text(reported, encoding='utf-8')
            now = marker.stat().st_mtime + age
            return deploy_local.worker_problem(root, NEW, lambda: now)

    def test_renewed_worker_is_healthy(self):
        self.assertEqual(self.worker_problem(NEW, 9999), '')

    def test_worker_has_time_to_renew_after_deploy(self):
        self.assertEqual(self.worker_problem(OLD, 60), '')

    def test_stale_worker_is_reported(self):
        self.assertIn(NEW[:7], self.worker_problem(OLD, 9999))

    def test_worker_that_reports_nothing_is_stale(self):
        self.assertIn(NEW[:7], self.worker_problem('', 9999))

    def test_crash_is_recorded_and_alerted(self):
        with patch.object(deploy_local, 'run', side_effect=RuntimeError('git fetch: x')), \
                patch.object(deploy_local, 'record') as record, \
                patch.object(deploy_local, 'notify') as notify:
            self.assertEqual(deploy_local.main(['--target', '.']), 1)
        self.assertIn('git fetch', record.call_args.args[2])
        self.assertIn('git fetch', notify.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
