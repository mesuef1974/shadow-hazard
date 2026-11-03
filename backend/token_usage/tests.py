import sqlite3
import tempfile
from pathlib import Path
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from workspace.models import Project
from .models import TrackedSession, TokenSample
from .services import synchronize,summarize


class TokenUsageTests(TestCase):
    def test_future_quota_and_boolean_are_not_current_numbers(self):
        from .models import CollectorState
        from django.utils import timezone
        from datetime import timedelta
        owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.client.force_login(owner)
        now=timezone.now()
        state=CollectorState.objects.create(key='local',payload={'weekly':{'weekly_used_percent':20,'sampled_at':(now+timedelta(hours=1)).isoformat(),'weekly_resets_at':(now+timedelta(days=1)).timestamp()}})
        self.assertTrue(self.client.get('/usage/').context['weekly']['historical'])
        state.payload['weekly']['weekly_used_percent']=True
        state.save()
        self.assertFalse(self.client.get('/usage/').context['weekly']['available'])

    def test_manual_sync_preserves_valid_filters(self):
        from unittest.mock import patch
        owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.client.force_login(owner)
        with patch('token_usage.views.synchronize'):
            response=self.client.post('/usage/sync/',{'session':self.session.pk,'period':'day'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,f'/usage/?session={self.session.pk}&period=day')

    def test_shared_samples_avoid_second_read_and_bound_only_chart(self):
        from .delegated import dashboard_samples
        from .analytics import analyze
        from django.utils import timezone
        from datetime import timedelta
        now=timezone.now()
        TokenSample.objects.bulk_create([TokenSample(session=self.session,source_id=i+1,sampled_at=now-timedelta(seconds=600-i),total=i,input=i,output=0,cached=0,reasoning=0,context=i,context_limit=1000) for i in range(600)])
        samples=dashboard_samples(self.project)
        with self.assertNumQueries(0):
            _, totals=summarize(self.project,samples=samples)
            data=analyze(self.project,samples=samples)
        self.assertEqual(totals['total'],599)
        self.assertEqual(data['measured'],599)
        self.assertEqual(len(data['context']),500)
        self.assertEqual(data['context'][-1]['tokens'],599)

    def test_historical_quota_and_unknown_context(self):
        from .models import CollectorState
        from .analytics import analyze
        from django.utils import timezone
        owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        CollectorState.objects.create(key='local',payload={'collector':{'heartbeat_at':4},'weekly':{'weekly_used_percent':47,'sampled_at':'invalid','weekly_resets_at':'invalid'}})
        self.client.force_login(owner)
        response=self.client.get('/usage/')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['weekly']['historical'])
        TokenSample.objects.create(session=self.session,source_id=1,sampled_at=timezone.now(),model='X',total=1,input=1,output=0,cached=0,reasoning=0,context=10,context_limit=0)
        self.assertIsNone(analyze(self.project)['context'][0]['percent'])

    def test_session_filter_is_project_scoped(self):
        from .analytics import analyze
        from django.utils import timezone
        other=TrackedSession.objects.create(external_id='second',name='جلسة ثانية',project=self.project)
        TokenSample.objects.create(session=other,source_id=1,sampled_at=timezone.now(),model='X',total=900,input=900,cached=0,output=0,reasoning=0,context=50,context_limit=100)
        sessions,totals=summarize(self.project,self.session.pk)
        self.assertEqual(len(sessions),1)
        self.assertEqual(totals['total'],0)
        self.assertEqual(analyze(self.project,session_id=self.session.pk)['context'],[])
        self.assertEqual(len(analyze(self.project,session_id=other.pk)['context']),1)

    def test_model_attribution_excludes_baseline_switches_and_resets(self):
        from datetime import timedelta
        from django.utils import timezone
        from .analytics import analyze
        now=timezone.now()
        for index,(model,total) in enumerate([('A',100),('A',150),('B',180),('B',220),('B',10),('B',30)]):
            TokenSample.objects.create(session=self.session,source_id=index,sampled_at=now+timedelta(seconds=index),model=model,total=total,input=total,output=0,cached=0,reasoning=0,context=20,context_limit=100)
        data=analyze(self.project)
        self.assertEqual(data['baseline'],100)
        self.assertEqual(data['boundary'],30)
        self.assertEqual(data['resets'],1)
        self.assertEqual(data['measured'],120)
        self.assertEqual(data['peak']['tokens'],120)
        self.assertEqual({row['name']:row['tokens'] for row in data['models']},{'A':50,'B':40})
        self.assertEqual(sum(row['value'] for row in data['composition']),120)
        for index,total in [(6,230),(7,240)]:
            TokenSample.objects.create(session=self.session,source_id=index,sampled_at=now+timedelta(seconds=index),model='B',total=total,input=total,output=0,cached=0,reasoning=0,context=20,context_limit=100)
        recovered=analyze(self.project)
        self.assertEqual(recovered['measured'],140)
        self.assertEqual(recovered['boundary'],40)
        self.assertEqual({row['name']:row['tokens'] for row in recovered['models']},{'A':50,'B':50})
        self.assertEqual(sum(row['value'] for row in recovered['composition']),130)

    def test_hourly_peak_is_absent_without_positive_usage(self):
        from django.utils import timezone
        from .analytics import analyze
        TokenSample.objects.create(session=self.session,source_id=1,sampled_at=timezone.now(),model='X',
            total=100,input=100,output=0,cached=0,reasoning=0,context=20,context_limit=100)
        self.assertIsNone(analyze(self.project)['peak'])

    def setUp(self):
        self.project=Project.objects.create(name='test')
        self.session=TrackedSession.objects.create(external_id='project-session',name='Project',project=self.project)

    def test_import_scope_idempotence_and_cumulative_total(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.sqlite3'
            connection=sqlite3.connect(path)
            connection.execute('CREATE TABLE usage_samples (sample_id INTEGER, session_id TEXT, sampled_at TEXT, model TEXT, cumulative_total_tokens INTEGER,cumulative_input_tokens INTEGER,cumulative_cached_input_tokens INTEGER,cumulative_output_tokens INTEGER,cumulative_reasoning_output_tokens INTEGER,context_total_tokens INTEGER,context_window_tokens INTEGER)')
            for row in [(1,'project-session',100),(2,'project-session',150),(3,'unrelated',900)]:
                input_tokens=row[2]-20
                connection.execute('INSERT INTO usage_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',(row[0],row[1],f'2026-10-31T06:00:0{row[0]}Z','model',row[2],input_tokens,20,20,5,50,100))
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
            connection.execute("INSERT INTO usage_samples VALUES (5,'project-session','2026-10-31T06:00:05Z','model',170,150,20,20,5,50,100)")
            connection.commit();connection.close()
            with override_settings(TOKEN_USAGE_SOURCE=path):
                failed=synchronize()
                self.assertFalse(failed.success)
                self.assertEqual(failed.added,1)
                retry=synchronize()
                self.assertFalse(retry.success)
                self.assertEqual(retry.added,0)
            self.assertEqual(TokenSample.objects.count(),3)
            self.assertEqual(summarize(self.project)[1]['total'],170)

    def test_corrected_source_replaces_generation_without_stale_totals(self):
        from .models import UsageSourceState
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.sqlite3'
            def write_source(total):
                with sqlite3.connect(path) as connection:
                    connection.execute('DROP TABLE IF EXISTS usage_samples')
                    connection.execute('CREATE TABLE usage_samples (sample_id INTEGER, session_id TEXT, sampled_at TEXT, model TEXT, cumulative_total_tokens INTEGER,cumulative_input_tokens INTEGER,cumulative_cached_input_tokens INTEGER,cumulative_output_tokens INTEGER,cumulative_reasoning_output_tokens INTEGER,context_total_tokens INTEGER,context_window_tokens INTEGER)')
                    connection.execute('INSERT INTO usage_samples VALUES (1,?,?,?,?,?,?,?,?,?,?)',
                        ('project-session','2026-10-31T06:00:01Z','model',total,total-20,20,20,5,50,100))
                connection.close()
            write_source(100)
            with override_settings(TOKEN_USAGE_SOURCE=path):
                self.assertEqual(synchronize().added,1)
                first=UsageSourceState.objects.get(key='local').generation
                write_source(180)
                result=synchronize()
                self.assertTrue(result.success)
            self.assertNotEqual(UsageSourceState.objects.get(key='local').generation,first)
            self.assertEqual(TokenSample.objects.count(),2) # prior generation retained
            self.assertEqual(summarize(self.project)[1]['total'],180)

    def test_database_rejects_inconsistent_sample_counters(self):
        from django.db import IntegrityError, transaction
        from django.utils import timezone
        with self.assertRaises(IntegrityError), transaction.atomic():
            TokenSample.objects.create(session=self.session,source_id=77,sampled_at=timezone.now(),
                model='X',total=20,input=10,cached=11,output=10,reasoning=0,context=0,context_limit=0)

    def test_missing_source_preserves_data_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(TOKEN_USAGE_SOURCE=Path(folder)/'missing.db'):
            run=synchronize()
            self.assertFalse(run.success)
            self.assertEqual(run.error_code,'source_unavailable')

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
