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


if __name__ == '__main__':
    unittest.main()
