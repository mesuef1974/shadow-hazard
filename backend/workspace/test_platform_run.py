import json
import os
import signal
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from .platform_run import (
    LOCK_HELD,
    NEXT_START,
    RESTART_LIMIT,
    STABLE_SECONDS,
    STARTED_BY,
    claim_start,
    guard,
    report_no_trace,
    tracked_run,
)


class TrackedRunTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.local = Path(folder.name)
        self.log = (self.local / 'server.log').open('a', encoding='utf-8')
        self.addCleanup(self.log.close)
        self.marker = self.local / 'platform-run.json'
        self.exits = self.local / 'platform-exits.jsonl'

    def lines(self):
        return [json.loads(line) for line in self.exits.read_text(encoding='utf-8').splitlines()]

    def test_normal_end_is_recorded_and_clears_the_trace(self):
        with tracked_run(self.local, self.log):
            self.assertTrue(self.marker.exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual([line['kind'] for line in self.lines()], ['exit'])

    def test_error_reason_is_kept_and_raised(self):
        with self.assertRaises(RuntimeError):
            with tracked_run(self.local, self.log):
                raise RuntimeError('سبب العطل')
        self.assertIn('سبب العطل', self.lines()[0]['reason'])

    def test_system_exit_code_is_kept(self):
        with self.assertRaises(SystemExit):
            with tracked_run(self.local, self.log):
                raise SystemExit(3)
        self.assertEqual(self.lines()[0]['reason'], 'SystemExit: 3')

    def test_signal_becomes_a_recorded_exit(self):
        before = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(SystemExit):
            with tracked_run(self.local, self.log):
                signal.raise_signal(signal.SIGTERM)
        self.assertEqual(self.lines()[0]['reason'], 'SystemExit: إشارة SIGTERM')
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_killed_run_is_reported_by_the_next_start(self):
        self.marker.write_text(json.dumps({'pid': 7, 'started': 'x'}), encoding='utf-8')
        os.utime(self.marker, (1_000_000, 1_000_000))
        with mock.patch.dict(os.environ, {STARTED_BY: 'deploy'}):
            report_no_trace(self.marker, self.exits)
        line = self.lines()[0]
        self.assertEqual((line['kind'], line['pid'], line['next_by']), ('no-trace', 7, 'deploy'))
        self.assertEqual(line['last_beat'], '1970-01-12T13:46:40+00:00')

    def test_task_is_the_default_starter(self):
        self.marker.write_text('{}', encoding='utf-8')
        with mock.patch.dict(os.environ):
            os.environ.pop(STARTED_BY, None)
            report_no_trace(self.marker, self.exits)
        self.assertEqual(self.lines()[0]['next_by'], 'task')

    def test_unreadable_trace_is_ignored(self):
        self.marker.write_text('not json', encoding='utf-8')
        report_no_trace(self.marker, self.exits)
        self.assertFalse(self.exits.exists())


class ClaimStartTests(TestCase):
    def test_deploy_request_is_read_once(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ):
            os.environ.pop(STARTED_BY, None)
            local = Path(folder)
            (local / NEXT_START).write_text('deploy', encoding='utf-8')
            claim_start(local)
            self.assertEqual(os.environ[STARTED_BY], 'deploy')
            self.assertFalse((local / NEXT_START).exists())

    def test_scheduled_start_stays_task(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ):
            os.environ.pop(STARTED_BY, None)
            claim_start(Path(folder))
            self.assertNotIn(STARTED_BY, os.environ)


class GuardTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.exits = Path(folder.name) / 'platform-exits.jsonl'
        patcher = mock.patch('workspace.platform_run.witnesses',
                             return_value={'battery_status': 2, 'recent': ['12:00:00 x.exe<-1 x']})
        patcher.start()
        self.addCleanup(patcher.stop)

    def lines(self):
        return [json.loads(line) for line in self.exits.read_text(encoding='utf-8').splitlines()]

    def serve(self, *codes):
        calls = iter(codes)

        def next_exit(_command):
            code = next(calls)
            if callable(code):
                return 9, code()
            return 9, code
        return mock.patch('workspace.platform_run.serve_once', side_effect=next_exit)

    def test_real_child_exit_code_is_recorded(self):
        with mock.patch.dict(os.environ, {STARTED_BY: 'deploy'}):
            guard([sys.executable, '-c', 'raise SystemExit(3)'], self.exits)
        first = self.lines()[0]
        self.assertEqual((first['kind'], first['code'], first['started_by']),
                         ('server-exit', '0x00000003', 'deploy'))

    def test_held_lock_is_not_an_exit(self):
        with self.serve(LOCK_HELD):
            guard(['server'], self.exits)
        self.assertFalse(self.exits.exists())

    def test_killed_server_is_restarted_with_its_witnesses(self):
        with mock.patch.dict(os.environ), self.serve(-1, LOCK_HELD):
            guard(['server'], self.exits)
        line = self.lines()[0]
        self.assertEqual((line['code'], line['next_by'], line['battery_status']),
                         ('0xffffffff', 'guard', 2))
        self.assertEqual(len(self.lines()), 1)

    def test_deploy_stop_is_not_restarted(self):
        (self.exits.parent / NEXT_START).write_text('deploy', encoding='utf-8')
        with self.serve(-1, AssertionError):
            guard(['server'], self.exits)
        self.assertEqual(self.lines()[0]['next_by'], 'deploy')
        self.assertNotIn('recent', self.lines()[0])

    def test_repeated_quick_falls_stop_the_loop(self):
        with mock.patch.dict(os.environ), self.serve(*[1] * RESTART_LIMIT):
            guard(['server'], self.exits)
        self.assertEqual(len(self.lines()), RESTART_LIMIT)
        self.assertEqual(self.lines()[-1]['next_by'], 'task')

    def test_stable_run_resets_the_count(self):
        clock = iter([0, 100, 100, 100 + STABLE_SECONDS * 2, 300, 301])
        with mock.patch.dict(os.environ), self.serve(1, 1, LOCK_HELD), \
                mock.patch('workspace.platform_run.time.monotonic', lambda: next(clock)):
            guard(['server'], self.exits)
        self.assertEqual([line['next_by'] for line in self.lines()], ['guard', 'guard'])

    def test_signals_are_ignored_only_while_waiting(self):
        before = signal.getsignal(signal.SIGTERM)
        seen = []

        def wait():
            seen.append(signal.getsignal(signal.SIGTERM))
            return LOCK_HELD
        with self.serve(wait):
            guard(['server'], self.exits)
        self.assertEqual(seen, [signal.SIG_IGN])
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
