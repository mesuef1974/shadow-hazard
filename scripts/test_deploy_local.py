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

    def watch(self, platform='', worker='', target=NEW, shown=True):
        with patch.object(deploy_local, 'platform_problem', return_value=platform), \
                patch.object(deploy_local, 'worker_problem', return_value=worker), \
                patch.object(deploy_local, 'notify', return_value=shown) as notify, \
                patch.object(deploy_local, 'record') as record:
            deploy_local.watch(Path('.'), NEW, target)
        return notify, record

    def test_stopped_platform_is_alerted_and_recorded(self):
        notify, record = self.watch(platform='المنصة لا تجيب على 8011')
        self.assertIn('8011', notify.call_args.args[1])
        self.assertEqual(record.call_args.args[:2], ('deploy', 'fail'))

    def test_platform_outranks_worker_in_the_single_alert(self):
        notify, _ = self.watch(platform='المنصة', worker='العامل')
        self.assertEqual(notify.call_args.args[1], 'المنصة')

    def test_recovery_is_never_recorded_as_a_deployment(self):
        notify, record = self.watch()
        self.assertEqual(notify.call_args.args[1], '')
        self.assertEqual(record.call_args.args[:3], ('deploy', 'info', 'زال العطل'))

    def test_quiet_cycle_records_nothing(self):
        _, record = self.watch(shown=False)
        record.assert_not_called()

    def test_failed_deploy_alert_survives_a_healthy_platform(self):
        notify, _ = self.watch(target=OLD)
        notify.assert_not_called()

    def test_stopped_platform_is_reported_even_after_a_failed_deploy(self):
        notify, _ = self.watch(platform='المنصة', target=OLD)
        self.assertEqual(notify.call_args.args[1], 'المنصة')

    def idle_cycle(self, current, target, problem=''):
        def git(root, *args):
            if args[0] == 'rev-parse':
                return target if args[1] == 'origin/main' else current
            return ''

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(deploy_local, 'refresh'), \
                patch.object(deploy_local, 'git', git), \
                patch.object(deploy_local, 'watch', return_value=problem), \
                patch.object(deploy_local, 'merge_small_update') as merge:
            root = Path(folder)
            marker = root / deploy_local.FAILED
            marker.parent.mkdir()
            marker.write_text(target if current != target else '', encoding='utf-8')
            self.assertEqual(deploy_local.run(root), 0)
        return merge

    def test_healthy_idle_cycle_merges_a_small_update(self):
        self.idle_cycle(NEW, NEW).assert_called_once()

    def test_no_update_on_top_of_a_problem(self):
        self.idle_cycle(NEW, NEW, 'المنصة').assert_not_called()

    def test_no_update_on_top_of_a_failed_deploy(self):
        self.idle_cycle(OLD, NEW).assert_not_called()

    def test_idle_check_stays_short(self):
        with patch.object(deploy_local, 'healthy', return_value=False) as healthy:
            self.assertIn('8011', deploy_local.platform_problem())
        healthy.assert_called_once_with(deploy_local.IDLE_TRIES)

    def test_crash_is_recorded_and_alerted(self):
        with patch.object(deploy_local, 'run', side_effect=RuntimeError('git fetch: x')), \
                patch.object(deploy_local, 'record') as record, \
                patch.object(deploy_local, 'notify') as notify:
            self.assertEqual(deploy_local.main(['--target', '.']), 1)
        self.assertIn('git fetch', record.call_args.args[2])
        self.assertIn('git fetch', notify.call_args.args[1])

    def test_platform_task_starts_the_server_and_knows_who_asked(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(deploy_local.subprocess, 'run') as run:
            root = Path(folder)
            (root / deploy_local.NEXT_START).parent.mkdir(parents=True)
            deploy_local.start_platform(root)
            self.assertEqual((root / deploy_local.NEXT_START).read_text(encoding='utf-8'), 'deploy')
        self.assertEqual(run.call_args.args[0],
                         ['schtasks', '/run', '/tn', deploy_local.PLATFORM_TASK])

    def test_start_is_retried_while_the_old_guard_holds_the_task(self):
        with patch.object(deploy_local, 'stop_platform'), \
                patch.object(deploy_local, 'start_platform') as start, \
                patch.object(deploy_local.time, 'sleep'), \
                patch.object(deploy_local, 'healthy', side_effect=[False, True]):
            self.assertTrue(deploy_local.restart(Path('.')))
        self.assertEqual(start.call_count, 2)

    def test_restart_gives_up_after_its_tries(self):
        with patch.object(deploy_local, 'stop_platform'), \
                patch.object(deploy_local, 'start_platform') as start, \
                patch.object(deploy_local.time, 'sleep'), \
                patch.object(deploy_local, 'healthy', return_value=False):
            self.assertFalse(deploy_local.restart(Path('.')))
        self.assertEqual(start.call_count, deploy_local.START_TRIES)


if __name__ == '__main__':
    unittest.main()
