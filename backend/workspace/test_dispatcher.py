from unittest.mock import patch
import uuid
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Project, ModelPolicy, SessionCharter, ModelJob
from .dispatcher import enqueue, run_one
from .codex_transport import Unavailable, Uncertain


class DispatcherTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.student=get_user_model().objects.create_user(username='saud',role='student')
        self.project=Project.objects.create(name='test')
        self.policy=ModelPolicy.objects.create(category='review',title='review',model='gpt-6-luna',thinking='low')
        self.charter=SessionCharter.objects.create(key='review',name='review',project=self.project,policy=self.policy,thread_id='01a190d6-6d72-7753-a9c7-1cb66e8b7129')

    def test_authorization_and_snapshot(self):
        with self.assertRaises(ValueError):enqueue(self.charter,self.student,'test')
        job=enqueue(self.charter,self.owner,'test')
        self.policy.model='gpt-6-astra';self.policy.save()
        job.refresh_from_db();self.assertEqual(job.requested_model,'gpt-6-luna')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/sessions/jobs/').status_code,403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/sessions/jobs/'),'الموصل قيد التحقق')

    def test_idempotency_and_uncertain_database_lock(self):
        key=uuid.uuid4()
        first=enqueue(self.charter,self.owner,'test',submission_key=key)
        self.assertEqual(enqueue(self.charter,self.owner,'test',submission_key=key).pk,first.pk)
        with self.assertRaises(ValueError):enqueue(self.charter,self.owner,'different',submission_key=key)
        first.state='uncertain';first.save()
        second=enqueue(self.charter,self.owner,'second')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ModelJob.objects.filter(pk=second.pk).update(state='running')

    @patch('workspace.dispatcher.shutil.which',return_value='codex.exe')
    @patch('workspace.codex_transport.execute')
    def test_completion_and_no_replay(self,execute,_which):
        execute.return_value={'completed':True,'text':'done','turn_id':'turn1'}
        enqueue(self.charter,self.owner,'test')
        self.assertEqual(run_one().state,'completed')
        self.assertIsNone(run_one());self.assertEqual(execute.call_count,1)

    @patch('workspace.dispatcher.shutil.which',return_value='codex.exe')
    @patch('workspace.codex_transport.execute',side_effect=Unavailable)
    def test_unavailable_is_blocked_without_retry(self,execute,_which):
        enqueue(self.charter,self.owner,'test')
        self.assertEqual(run_one().state,'blocked')
        self.assertIsNone(run_one());self.assertEqual(execute.call_count,1)

    @patch('workspace.dispatcher.shutil.which',return_value='codex.exe')
    @patch('workspace.codex_transport.execute',side_effect=Uncertain)
    def test_uncertain_stops_following_jobs(self,execute,_which):
        enqueue(self.charter,self.owner,'first');enqueue(self.charter,self.owner,'second')
        self.assertEqual(run_one().state,'uncertain')
        self.assertIsNone(run_one());self.assertEqual(execute.call_count,1)
        self.assertEqual(ModelJob.objects.filter(state='queued').count(),1)
