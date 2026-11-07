import atexit
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from . import container_reaper as reaper
from .docker_runtime import Runtime

NOW = 1_000_000.0


class FakeDocker:
    """يحاكي docker: قوائم جاهزة، ويسجل ما يُحذف."""

    def __init__(self, containers=(), volumes=(), fail=None):
        self.containers, self.volumes, self.fail = containers, volumes, fail
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command[1:])
        if self.fail:
            raise self.fail
        args = command[1:]
        if args[:2] == ['ps', '-a']:
            return SimpleNamespace(returncode=0, stdout='\n'.join(self.containers))
        if args[:2] == ['volume', 'ls']:
            return SimpleNamespace(returncode=0, stdout='\n'.join(self.volumes))
        return SimpleNamespace(returncode=0, stdout='')

    def removed(self):
        return [c for c in self.calls if c[0] == 'rm' or c[:2] == ['volume', 'rm']]


class ReaperTests(TestCase):
    def setUp(self):
        reaper._last['at'] = None

    def test_only_expired_items_are_removed(self):
        old, fresh = int(NOW - reaper.TTL_SECONDS - 1), int(NOW - 60)
        fake = FakeDocker([f'old-worker\t{old}', f'fresh-worker\t{fresh}'],
                          [f'old-volume\t{old}', f'fresh-volume\t{fresh}'])
        removed = reaper.reap(fake, now=lambda: NOW)
        self.assertEqual(removed, {'containers': ['old-worker'], 'volumes': ['old-volume']})
        self.assertEqual(fake.removed(), [['rm', '--force', 'old-worker'],
                                          ['volume', 'rm', 'old-volume']])

    def test_unknown_age_is_never_touched(self):
        fake = FakeDocker(['mystery\t', 'bad\tabc'], ['odd\t-1'])
        reaper.reap(fake, now=lambda: NOW)
        self.assertEqual(fake.removed(), [])

    def test_listing_is_limited_to_the_project_label(self):
        fake = FakeDocker()
        reaper.reap(fake, now=lambda: NOW)
        for call in fake.calls:
            self.assertIn(f'label={reaper.OWNER_LABEL}={reaper.OWNER}', call)

    def test_missing_docker_is_silent_not_an_error(self):
        fake = FakeDocker(fail=FileNotFoundError('docker'))
        self.assertEqual(reaper.reap(fake, now=lambda: NOW), {'containers': [], 'volumes': []})
        self.assertEqual(reaper.maintenance(run=fake), {'errors': []})

    def test_maintenance_runs_at_most_every_five_minutes(self):
        fake = FakeDocker()
        now = [0]
        for now[0] in (0, 10, 299, 301):
            reaper.maintenance(clock=lambda: now[0], run=fake)
        listings = [c for c in fake.calls if c[:2] == ['ps', '-a']]
        self.assertEqual(len(listings), 2)

    def test_removal_never_uses_a_window_or_a_model(self):
        self.assertEqual(reaper.NO_WINDOW, getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        source = Path(reaper.__file__).read_text(encoding='utf-8')
        for word in ('codex', 'claude', 'openai', 'anthropic'):
            self.assertNotIn(word, source.lower())

    def test_labels_carry_owner_and_creation_time(self):
        self.assertEqual(reaper.labels(123), ['--label', 'shadow-hazard.owner=readonly-runtime',
                                              '--label', 'shadow-hazard.created=123'])


class RuntimeCleanupTests(TestCase):
    def start(self, folder):
        home = Path(folder) / '.codex'
        home.mkdir()
        (home / 'auth.json').write_text('{}')
        runtime = Runtime(folder)
        with patch('execution_context.docker_runtime.Path.home', return_value=Path(folder)), \
                patch('execution_context.docker_runtime.Runtime.docker') as docker, \
                patch('execution_context.docker_runtime.subprocess.Popen') as popen:
            runtime.start(['codex', 'app-server'])
        return runtime, docker, popen

    def test_every_container_and_volume_is_labelled_for_the_reaper(self):
        with TemporaryDirectory() as folder, patch('atexit.register'):
            _, docker, popen = self.start(folder)
        owner = f'{reaper.OWNER_LABEL}={reaper.OWNER}'
        for call in docker.call_args_list:
            if call.args[0] in ('run', 'volume'):
                self.assertIn(owner, call.args)
        self.assertIn(owner, popen.call_args.args[0])

    def test_close_is_registered_with_atexit_and_survives_missing_docker(self):
        with TemporaryDirectory() as folder, patch('atexit.register') as register:
            runtime, _, _ = self.start(folder)
        register.assert_called_once_with(runtime.close)
        missing = patch('execution_context.docker_runtime.subprocess.run',
                        side_effect=FileNotFoundError)
        with missing, patch.object(atexit, 'unregister') as unregister:
            runtime.close()
        unregister.assert_called_once_with(runtime.close)
