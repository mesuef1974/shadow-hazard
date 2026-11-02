import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from workspace.models import Project
from .locking import local_lock
from .services import synchronize


class AutoSyncTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(LOCAL_DIR=Path(self.directory.name))
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_manual_sync_skips_while_another_sync_owns_lock_and_recovers(self):
        with patch('token_usage.services._synchronize', return_value='finished') as perform:
            with local_lock('token-usage-sync.lock') as held:
                self.assertTrue(held)
                self.assertIsNone(synchronize())
                perform.assert_not_called()
            self.assertEqual(synchronize(), 'finished')

    def test_watch_is_single_instance_and_defaults_to_sixty_seconds(self):
        run = SimpleNamespace(success=True, added=0, message='ok')
        with patch('token_usage.management.commands.sync_token_usage.synchronize', return_value=run) as sync:
            with local_lock('token-usage-watch.lock'):
                call_command('sync_token_usage', watch=True)
            sync.assert_not_called()
            with patch('token_usage.management.commands.sync_token_usage.time.sleep', side_effect=KeyboardInterrupt) as sleep:
                with self.assertRaises(KeyboardInterrupt):
                    call_command('sync_token_usage', watch=True)
                sleep.assert_called_once_with(60)
            sync.assert_called_once()

    def test_refresh_get_is_supervisor_only_and_does_not_trigger_sync(self):
        Project.objects.create(name='ظل الخطر')
        owner = get_user_model().objects.create_user(username='mesuef', role='supervisor')
        student = get_user_model().objects.create_user(username='saud', role='student')
        self.assertEqual(self.client.get('/usage/').status_code, 302)
        self.client.force_login(student)
        self.assertEqual(self.client.get('/usage/').status_code, 403)
        self.client.force_login(owner)
        with patch('token_usage.views.synchronize') as sync:
            response = self.client.get('/usage/?period=day')
            sync.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['analytics']['period'], 'day')
        self.assertContains(response, 'usage-refresh.js')
        self.assertContains(response, 'وقت مزامنة البيانات الفعلي')

    def test_invalid_interval_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command('sync_token_usage', watch=True, interval=0)
