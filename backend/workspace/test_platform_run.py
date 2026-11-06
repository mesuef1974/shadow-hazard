import json
import os
import signal
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from .platform_run import STARTED_BY, report_no_trace, tracked_run


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
