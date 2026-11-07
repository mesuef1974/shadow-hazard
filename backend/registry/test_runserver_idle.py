import threading
from unittest import TestCase

from registry.management.commands.runserver import Command, IdleExit


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class IdleExitTests(TestCase):
    def test_expires_only_after_the_idle_limit(self):
        clock = Clock()
        idle = IdleExit(5, clock)
        clock.now = 299
        self.assertFalse(idle.expired())
        clock.now = 300
        self.assertTrue(idle.expired())

    def test_a_request_resets_the_timer(self):
        clock = Clock()
        idle = IdleExit(5, clock)
        clock.now = 290
        idle.touch()
        clock.now = 580
        self.assertFalse(idle.expired())

    def test_zero_minutes_never_expires(self):
        clock = Clock()
        idle = IdleExit(0, clock)
        clock.now = 10 ** 9
        self.assertFalse(idle.expired())

    def test_watcher_interrupts_once_when_idle(self):
        clock = Clock()
        idle = IdleExit(1, clock)
        clock.now = 60
        calls = []
        idle.watch(threading.Event(), interval=0, interrupt=lambda: calls.append(1))
        self.assertEqual(calls, [1])

    def test_watcher_stops_without_interrupting_when_asked(self):
        idle = IdleExit(1, Clock())
        stop = threading.Event()
        stop.set()
        calls = []
        idle.watch(stop, interval=0, interrupt=lambda: calls.append(1))
        self.assertEqual(calls, [])

    def test_option_defaults_to_off(self):
        parser = Command().create_parser('manage.py', 'runserver')
        self.assertEqual(parser.parse_args([]).idle_exit, 0)
        self.assertEqual(parser.parse_args(['--idle-exit', '10']).idle_exit, 10)
