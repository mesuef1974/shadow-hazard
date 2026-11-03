from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from workspace.models import Project, ModelJob, SessionCharter, ModelPolicy
from .models import TrackedSession, TokenSample
from .services import summarize, synchronize
from .analytics import analyze


class DelegatedUsageTests(TestCase):
    def setUp(self):
        self.project=Project.objects.create(name='test')
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        policy=ModelPolicy.objects.create(category='coordination',title='coordination')
        charter=SessionCharter.objects.create(key='coordination',name='coordination',project=self.project,policy=policy)
        self.session=TrackedSession.objects.create(external_id='execution-4',name='طلب 4',project=self.project)
        self.job=ModelJob.objects.create(charter=charter,owner=self.owner,prompt='test',
            requested_model='gpt-6-luna',requested_thinking='low',observed_model='gpt-6-luna',
            execution_thread_id=self.session.external_id,state='completed',finished_at=timezone.now(),
            token_usage={'input_tokens':21468,'cached_input_tokens':7296,'output_tokens':12,'reasoning_output_tokens':0})

    def test_direct_usage_visible_without_collector_and_cached_not_added(self):
        sessions,totals=summarize(self.project)
        self.assertEqual(totals,{'total':21480,'input':21468,'cached':7296,'output':12,'reasoning':0})
        self.assertEqual(sessions[0]['delegated_job'].pk,self.job.pk)
        chart=analyze(self.project)
        self.assertEqual(chart['measured'],21480)
        self.assertEqual(chart['models'][0]['tokens'],21480)
        self.assertEqual(sum(row['value'] for row in chart['composition']),21480)
        self.assertEqual(chart['context'],[]) # no invented context window
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/usage/'),'21480')
        with TemporaryDirectory() as folder,override_settings(TOKEN_USAGE_SOURCE=Path(folder)/'missing.db'):
            self.assertFalse(synchronize().success)
        self.assertEqual(summarize(self.project)[1]['total'],21480)

    def add_sample(self,total=21480):
        return TokenSample.objects.create(session=self.session,source_id=1,sampled_at=timezone.now(),
            model='gpt-6-luna',total=total,input=total-12,cached=7296,output=12,reasoning=0,context=200,context_limit=1000)

    def test_late_collector_does_not_double_count_or_change_model_totals(self):
        before=analyze(self.project)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'collector.db'
            with sqlite3.connect(path) as source:
                source.execute('CREATE TABLE usage_samples (sample_id INTEGER,session_id TEXT,sampled_at TEXT,model TEXT,cumulative_total_tokens INTEGER,cumulative_input_tokens INTEGER,cumulative_cached_input_tokens INTEGER,cumulative_output_tokens INTEGER,cumulative_reasoning_output_tokens INTEGER,context_total_tokens INTEGER,context_window_tokens INTEGER)')
                source.execute('INSERT INTO usage_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (1,self.session.external_id,timezone.now().isoformat(),'gpt-6-luna',21480,21468,7296,12,0,200,1000))
            source.close()
            with override_settings(TOKEN_USAGE_SOURCE=path):
                self.assertEqual(synchronize().added,1)
                self.assertEqual(synchronize().added,0)
        for _ in range(3):
            self.assertEqual(summarize(self.project)[1]['total'],21480)
            self.assertEqual(analyze(self.project)['models'],before['models'])
        self.assertEqual(TokenSample.objects.count(),1) # no copied or synthetic DB samples

    def test_later_cumulative_growth_selected_once(self):
        self.add_sample(22000)
        sessions,totals=summarize(self.project)
        self.assertEqual(totals['total'],22000)
        self.assertEqual(sessions[0]['delegated_job'].pk,self.job.pk)
        chart=analyze(self.project)
        self.assertEqual(chart['measured'],22000)
        self.assertEqual(chart['models'][0]['tokens'],22000)
        self.assertEqual(sum(row['value'] for row in chart['composition']),22000)

    def test_multiple_direct_jobs_on_one_execution_thread_are_summed(self):
        second=ModelJob.objects.create(charter=self.job.charter,owner=self.owner,prompt='second turn',
            requested_model='gpt-6-luna',requested_thinking='low',observed_model='gpt-6-luna',
            execution_thread_id=self.session.external_id,state='completed',finished_at=timezone.now(),
            token_usage={'input_tokens':90,'cached_input_tokens':20,'output_tokens':10,'reasoning_output_tokens':2})
        sessions,totals=summarize(self.project)
        self.assertEqual(totals['total'],21580)
        self.assertEqual(sessions[0]['direct_job_count'],2)
        self.assertIsNone(sessions[0]['delegated_job'].pk)
        self.assertEqual(analyze(self.project)['measured'],21580)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/usage/'),'استهلاك مباشر مجمع')

    def test_filters_and_invalid_counters(self):
        other=Project.objects.create(name='other')
        self.assertEqual(summarize(other)[1]['total'],0)
        self.assertEqual(summarize(self.project,999)[1]['total'],0)
        self.job.finished_at=timezone.now()-timedelta(days=8);self.job.save()
        self.assertEqual(analyze(self.project,'week')['measured'],0)
        self.job.token_usage['cached_input_tokens']=99999;self.job.save()
        self.assertEqual(summarize(self.project)[1]['total'],0)

    def test_unobserved_model_is_not_attributed_to_requested_model(self):
        self.job.observed_model='';self.job.save()
        chart=analyze(self.project)
        self.assertEqual(chart['models'],[])
        self.assertEqual(chart['boundary'],21480)
