from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.db import OperationalError
from .worker_lock import single_worker
from .models import DispatcherRun, DispatcherHeartbeat, Project, SessionCharter
from .dispatcher import resolve_codex


class WorkerTests(TestCase):
    @patch('workspace.dispatcher.shutil.which',return_value=None)
    def test_logon_environment_without_codex_on_path(self,which):
        with TemporaryDirectory() as folder,patch.dict('os.environ',{'LOCALAPPDATA':folder}):
            executable=Path(folder)/'OpenAI'/'Codex'/'bin'/'version'/'codex.exe'
            executable.parent.mkdir(parents=True);executable.write_bytes(b'placeholder')
            self.assertEqual(resolve_codex(),str(executable))
    def test_lock_rejects_duplicate_and_releases(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'worker.lock'
            with single_worker(path) as first:
                self.assertTrue(first)
                with single_worker(path) as second:self.assertFalse(second)
            with single_worker(path) as third:self.assertTrue(third)

    @patch('workspace.management.commands.run_model_dispatcher.run_one',return_value=None)
    def test_run_history_and_clean_stop(self,run_one):
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)):
            call_command('run_model_dispatcher')
        health=DispatcherHeartbeat.objects.get()
        self.assertEqual(health.state,'stopped')
        self.assertIsNotNone(health.stopped_at)
        self.assertEqual(DispatcherRun.objects.get().state,'stopped')
        run_one.assert_called_once()

    @patch('workspace.management.commands.run_model_dispatcher.run_one',side_effect=OperationalError('temporary database failure'))
    def test_database_failure_does_not_report_healthy_worker(self,run_one):
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)):
            with self.assertRaises(OperationalError):call_command('run_model_dispatcher',watch=True)
        self.assertEqual(DispatcherHeartbeat.objects.get().state,'failed')
        self.assertEqual(DispatcherRun.objects.get().state,'failed')

    @patch('workspace.management.commands.run_model_dispatcher.run_one')
    def test_duplicate_does_not_touch_health(self,run_one):
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)):
            with single_worker(settings.BASE_DIR/'.local'/'model-dispatcher.lock'):
                output=StringIO();call_command('run_model_dispatcher',stdout=output)
                self.assertIn('لن تبدأ نسخة ثانية',output.getvalue())
                self.assertFalse(DispatcherRun.objects.exists())
        run_one.assert_not_called()

    def test_configuration_idempotent_and_main_charter_present(self):
        Project.objects.create(name='ظل الخطر')
        for _ in range(2):call_command('configure_local_dispatcher',startup_method='Windows — عند تسجيل الدخول',stdout=StringIO())
        self.assertEqual(SessionCharter.objects.filter(key='coordination').count(),1)
        charter=SessionCharter.objects.get(key='coordination')
        self.assertEqual(charter.policy.model,'gpt-6-luna')
        self.assertEqual(charter.thread_id,'01a18e02-0487-7180-9aa0-f932b22c8ef4')
