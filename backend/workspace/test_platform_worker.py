from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import DispatcherHeartbeat, DispatcherRun


@override_settings(CODEX_RETIRED=True)
class PlatformWorkerTests(TestCase):
    def test_runs_only_backup_and_claude_import(self):
        DispatcherHeartbeat.objects.create(key='session_bridge', state='running', pid=9)
        with patch('operations.services.run_due_backup', return_value={'due': False}) as backup, \
                patch('claude_usage.services.import_usage') as usage, \
                patch('execution_context.delivery.deliver_one') as codex_delivery, \
                patch('workspace.dispatcher.run_one') as codex_jobs:
            usage.return_value.ok = True
            call_command('run_model_dispatcher', stdout=StringIO())
        backup.assert_called_once(); usage.assert_called_once()
        codex_delivery.assert_not_called(); codex_jobs.assert_not_called()
        self.assertEqual(DispatcherHeartbeat.objects.get(key='session_bridge').state, 'retired')
        local = DispatcherHeartbeat.objects.get(key='local')
        self.assertEqual((local.state, local.last_error), ('stopped', ''))
        self.assertEqual(DispatcherRun.objects.get().state, 'stopped')

    def test_failed_step_is_visible_and_the_other_still_runs(self):
        with patch('operations.services.run_due_backup', side_effect=OSError('disk')), \
                patch('claude_usage.services.import_usage') as usage:
            usage.return_value.ok = True
            call_command('run_model_dispatcher', stdout=StringIO())
        usage.assert_called_once()
        self.assertIn('backup: OSError', DispatcherHeartbeat.objects.get(key='local').last_error)
