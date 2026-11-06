import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deploy_local

NEW, OLD = 'new1234567', 'old1234567'
SAVED = {'django==5.2.18', 'sqlparse==0.6.0'}


class DeployLocalTests(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(deploy_local.plan('a', 'a', False), 'idle')
        self.assertEqual(deploy_local.plan('a', 'b', False), 'deploy')
        self.assertEqual(deploy_local.plan('a', 'b', True), 'refuse')

    def test_failed_commit_is_not_retried_but_a_newer_one_is(self):
        self.assertEqual(deploy_local.plan('a', 'b', False, 'b'), 'idle')
        self.assertEqual(deploy_local.plan('a', 'c', False, 'b'), 'deploy')

    def attempt(self, run_ok=True, healthy=True, restored=True, back_up=True):
        moves = []
        calls = []
        answers = iter([healthy, back_up])

        def restart(root):
            calls.append('platform')
            return next(answers)

        def restore(root, saved):
            calls.append(('packages', saved))
            return restored

        with patch.object(deploy_local, 'steps', return_value=[('الهجرة', ['x'])]), \
                patch.object(deploy_local, 'freeze', return_value=SAVED), \
                patch.object(deploy_local, 'restore_packages', restore), \
                patch.object(deploy_local, 'stop_platform', lambda: calls.append('stop')):
            result = deploy_local.deploy(Path('.'), NEW, moves.append, lambda r, c: run_ok,
                                         restart, OLD)
        self.calls = calls
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

    def test_rollback_restores_packages_with_the_platform_stopped(self):
        self.attempt(run_ok=False)
        self.assertEqual(self.calls, ['stop', ('packages', SAVED), 'platform'])

    def test_rollback_after_an_unhealthy_start_restores_packages(self):
        (_, reason), _ = self.attempt(healthy=False)
        self.assertEqual(self.calls, ['platform', 'stop', ('packages', SAVED), 'platform'])
        self.assertNotIn('لم تعد', reason)

    def test_success_leaves_packages_alone(self):
        self.attempt()
        self.assertEqual(self.calls, ['platform'])

    def test_failed_restore_is_reported(self):
        (ok, reason), _ = self.attempt(run_ok=False, restored=False)
        self.assertFalse(ok)
        self.assertIn('لم تعد التبعيات', reason)

    def test_platform_that_does_not_return_is_reported(self):
        (_, reason), _ = self.attempt(healthy=False, back_up=False)
        self.assertIn('لم تعد المنصة', reason)

    def restore(self, states, run_ok=True):
        commands = []

        def run(root, command):
            commands.append(command)
            return run_ok

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(deploy_local, 'python_of', return_value='python'), \
                patch.object(deploy_local, 'freeze', side_effect=states):
            ok = deploy_local.restore_packages(Path(folder), SAVED, run)
            listed = (Path(folder) / deploy_local.PACKAGES)
            text = listed.read_text(encoding='utf-8') if listed.exists() else ''
        return ok, commands, text

    def test_unchanged_packages_need_no_pip(self):
        self.assertEqual(self.restore([SAVED]), (True, [], ''))

    def test_upgrade_returns_and_addition_is_removed(self):
        changed = {'django==6.1.2', 'sqlparse==0.6.0', 'New_Lib==1.0'}
        ok, commands, text = self.restore([changed, SAVED])
        self.assertTrue(ok)
        self.assertEqual(commands[0][-4:], ['uninstall', '-q', '-y', 'new_lib'])
        self.assertIn('--no-deps', commands[1])
        self.assertIn('django==5.2.18', text)

    def test_restore_is_not_trusted_without_a_second_look(self):
        self.assertFalse(self.restore([{'django==6.1.2'}, {'django==6.1.2'}])[0])

    def test_failed_pip_is_a_failed_restore(self):
        self.assertFalse(self.restore([{'django==6.1.2'}], run_ok=False)[0])

    def test_unreadable_environment_is_a_failed_restore(self):
        self.assertFalse(self.restore(RuntimeError('pip freeze: x'))[0])

    def test_nothing_moves_when_the_environment_cannot_be_read(self):
        moves = []
        with patch.object(deploy_local, 'freeze', side_effect=RuntimeError('pip freeze: x')), \
                self.assertRaises(RuntimeError):
            deploy_local.deploy(Path('.'), NEW, moves.append, None, None, OLD)
        self.assertEqual(moves, [])

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
