import sqlite3
import tempfile
from pathlib import Path
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from workspace.models import Project
from .models import TrackedSession, TokenSample
from .services import synchronize,summarize


class TokenUsageTests(TestCase):
    def setUp(self):
        self.project=Project.objects.create(name='test')
        self.session=TrackedSession.objects.create(external_id='project-session',name='Project',project=self.project)

    def test_import_scope_idempotence_and_cumulative_total(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.sqlite3'
            connection=sqlite3.connect(path)
            connection.execute('CREATE TABLE usage_samples (sample_id INTEGER, session_id TEXT, sampled_at TEXT, model TEXT, cumulative_total_tokens INTEGER,cumulative_input_tokens INTEGER,cumulative_cached_input_tokens INTEGER,cumulative_output_tokens INTEGER,cumulative_reasoning_output_tokens INTEGER,context_total_tokens INTEGER,context_window_tokens INTEGER)')
            for row in [(1,'project-session',100),(2,'project-session',150),(3,'unrelated',900)]:
                connection.execute('INSERT INTO usage_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',(row[0],row[1],f'2026-10-31T06:00:0{row[0]}Z','model',row[2],80,20,20,5,50,100))
            connection.commit();connection.close()
            with override_settings(TOKEN_USAGE_SOURCE=path):
                self.assertEqual(synchronize().added,2)
                self.assertEqual(synchronize().added,0)
            sessions,totals=summarize(self.project)
            self.assertEqual(totals['total'],150)
            self.assertEqual(sessions[0]['history'][0]['delta'],50)
            self.assertEqual(TokenSample.objects.count(),2)
            connection=sqlite3.connect(path)
            connection.execute("INSERT INTO usage_samples VALUES (4,'project-session','2026-10-31T06:00:04Z','model',-1,0,0,0,0,0,100)")
            connection.commit();connection.close()
            with override_settings(TOKEN_USAGE_SOURCE=path):
                self.assertFalse(synchronize().success)
            self.assertEqual(TokenSample.objects.count(),2)

    def test_missing_source_preserves_data_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(TOKEN_USAGE_SOURCE=Path(folder)/'missing.db'):
            self.assertFalse(synchronize().success)

    def test_supervisor_only_and_post_only(self):
        User=get_user_model()
        student=User.objects.create_user(username='saud',role='student')
        owner=User.objects.create_user(username='mesuef',role='supervisor')
        self.assertEqual(self.client.get('/usage/').status_code,302)
        self.client.force_login(student)
        self.assertEqual(self.client.get('/usage/').status_code,403)
        self.assertEqual(self.client.post('/usage/sync/').status_code,403)
        self.client.force_login(owner)
        self.assertEqual(self.client.get('/usage/').status_code,200)
        self.assertEqual(self.client.get('/usage/sync/').status_code,405)
