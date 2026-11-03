from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from django.test import TransactionTestCase
from django.db import OperationalError
from django.contrib.auth import get_user_model
from .worker_lock import single_worker
from .models import DispatcherRun, DispatcherHeartbeat, Project, SessionCharter, ModelPolicy, ModelJob
from .dispatcher import resolve_codex
from execution_context.models import SessionDispatch


class WorkerTests(TransactionTestCase):
    @patch('workspace.worker_startup.time.sleep')
    def test_startup_retries_busy_transaction_and_creates_one_run(self, sleep):
        from workspace.worker_startup import prepare_startup, recover_state
        from django.db import connection
        if connection.vendor != 'sqlite': self.skipTest('SQLite contention regression')
        attempts = []
        def attempt(pid):
            attempts.append(pid)
            if len(attempts) == 1:
                with patch('workspace.worker_startup.ModelJob.objects.filter', side_effect=OperationalError('database is locked')):
                    return recover_state(pid)
            return recover_state(pid)
        with patch('workspace.worker_startup.recover_state', side_effect=attempt):
            prepare_startup(123)
        self.assertEqual(DispatcherRun.objects.count(), 1)
        self.assertEqual(DispatcherHeartbeat.objects.count(), 3)
        sleep.assert_called_once()

    @patch('workspace.worker_startup.recover_state', side_effect=OperationalError('no such table'))
    def test_startup_failure_invalidates_previous_healthy_state(self, recovery):
        from workspace.worker_startup import prepare_startup
        DispatcherHeartbeat.objects.create(key='local', pid=1, state='running')
        with self.assertRaises(OperationalError): prepare_startup(123)
        self.assertEqual(DispatcherHeartbeat.objects.get(key='local').state, 'failed')
        recovery.assert_called_once()
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

    @patch('workspace.management.commands.run_model_dispatcher.ReadonlyRunner.start_if_pending')
    @patch('workspace.management.commands.run_model_dispatcher.run_one',return_value=None)
    def test_run_history_and_clean_stop(self,run_one,start_readonly):
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)):
            call_command('run_model_dispatcher')
        health=DispatcherHeartbeat.objects.get(key='local')
        self.assertEqual(health.state,'stopped')
        self.assertIsNotNone(health.stopped_at)
        self.assertEqual(DispatcherRun.objects.get().state,'stopped')
        run_one.assert_called_once()
        start_readonly.assert_not_called()

    @patch('workspace.management.commands.run_model_dispatcher.run_one',side_effect=OperationalError('temporary database failure'))
    def test_database_failure_does_not_report_healthy_worker(self,run_one):
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)):
            with self.assertRaises(OperationalError):call_command('run_model_dispatcher',watch=True)
        self.assertEqual(DispatcherHeartbeat.objects.get(key='local').state,'failed')
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

    @patch('workspace.management.commands.run_model_dispatcher.run_one',return_value=None)
    def test_preparer_restart_preserves_acknowledged_desktop_turn(self,run_one):
        owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        project=Project.objects.create(name='bridge test')
        policy=ModelPolicy.objects.create(category='coordination',title='coordination')
        charter=SessionCharter.objects.create(key='coordination',name='coordination',project=project,policy=policy,
            thread_id='01a18e02-0487-7180-9aa0-f932b22c8ef4',state='ready_waiting_for_task')
        job=ModelJob.objects.create(charter=charter,owner=owner,prompt='active desktop turn',requested_model='gpt-6-luna',
            requested_thinking='low',state='running')
        dispatch=SessionDispatch.objects.create(job=job,dispatch_key=__import__('uuid').uuid4(),target_charter=charter,
            target_thread_id=charter.thread_id,category='text',requested_model='gpt-6-luna',requested_thinking='low',
            route_reason='test',state='acknowledged')
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)):
            call_command('run_model_dispatcher')
        job.refresh_from_db();dispatch.refresh_from_db()
        self.assertEqual(job.state,'running')
        self.assertEqual(dispatch.state,'acknowledged')
