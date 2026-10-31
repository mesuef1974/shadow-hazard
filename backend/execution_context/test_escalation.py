import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from workspace.models import Project, ModelJob, ModelPolicy, SessionCharter
from workspace.dispatcher import enqueue, run_one
from token_usage.models import TrackedSession
from token_usage.services import summarize
from .escalation import propose, decide, authorized, detect_proposal
from .models import EscalationRequest


class EscalationTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.student=get_user_model().objects.create_user(username='saud',role='student')
        self.project=Project.objects.create(name='test')
        policy=ModelPolicy.objects.create(category='coordination',title='coordination',model='gpt-6-astra',thinking='high')
        self.charter=SessionCharter.objects.create(key='coordination',name='coordination',project=self.project,policy=policy,
            thread_id='01a18e02-0487-7180-9aa0-f932b22c8ef4',scope='test scope',rules='test rules')
        self.source=enqueue(self.charter,self.owner,'مراجعة برهان طويل')
        self.source.state='completed';self.source.finished_at=timezone.now()
        self.source.token_usage={'input_tokens':100,'cached_input_tokens':20,'output_tokens':10,'reasoning_output_tokens':0}
        self.source.save()

    def proposal(self):
        return propose(self.source,'gpt-6.1-sol','البرهان متعدد الحالات ولم أستطع التحقق من الحالة الحدية المحددة.')

    def test_legacy_job_without_frozen_context_is_rejected(self):
        self.source.task_context.delete()
        with self.assertRaises(ValueError):self.proposal()
        self.assertFalse(EscalationRequest.objects.exists())

    def test_luna_default_and_proposal_does_not_execute(self):
        self.assertEqual(self.source.requested_model,'gpt-6-luna')
        self.assertEqual(self.source.requested_thinking,'low')
        request=self.proposal()
        self.assertEqual(request.state,'pending');self.assertIsNone(request.approved_job)
        self.assertEqual(ModelJob.objects.count(),1)
        self.assertIsNone(run_one())

    def test_model_request_is_only_advice(self):
        self.source.result='ESCALATION_REQUEST: '+json.dumps({'model':'gpt-6.1-sol','reason':'البرهان يحتاج تدقيق حالة حدية لم أستطع حسمها بالأدلة المتاحة.'})
        detect_proposal(self.source)
        self.source.refresh_from_db()
        self.assertEqual(self.source.state,'needs_review')
        self.assertEqual(EscalationRequest.objects.get().state,'pending')
        self.assertEqual(ModelJob.objects.count(),1)

    def test_approval_once_and_bound_to_content(self):
        request=self.proposal()
        approved=decide(request.pk,self.owner,True,'محاولة واحدة لفحص العائق')
        self.assertTrue(authorized(approved.approved_job))
        again=decide(request.pk,self.owner,True)
        self.assertEqual(again.approved_job_id,approved.approved_job_id)
        self.assertEqual(ModelJob.objects.count(),2)
        approved.approved_job.prompt='مهمة مختلفة';approved.approved_job.save()
        self.assertFalse(authorized(approved.approved_job))

    def test_rejection_permissions_and_changed_source(self):
        request=self.proposal()
        with self.assertRaises(ValueError):decide(request.pk,self.student,True)
        self.source.prompt='changed';self.source.save()
        with self.assertRaises(ValueError):decide(request.pk,self.owner,True)
        request.refresh_from_db();self.assertEqual(request.state,'pending')
        decide(request.pk,self.owner,False,'لا حاجة للتصعيد')
        decide(request.pk,self.owner,True)
        request.refresh_from_db();self.assertEqual(request.state,'rejected')
        self.assertEqual(ModelJob.objects.count(),1)

    def test_http_permissions_post_only_and_csrf(self):
        request=self.proposal();url=f'/sessions/escalations/{request.pk}/decision/'
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(url,{'decision':'approve'}).status_code,403)
        self.assertEqual(self.client.get('/sessions/escalations/').status_code,403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code,405)
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.owner)
        self.assertEqual(secure.post(url,{'decision':'approve'}).status_code,403)
        self.assertContains(self.client.get('/sessions/escalations/'),'موافقة وتشغيل محاولة واحدة')

    @patch('workspace.codex_transport.execute')
    def test_worker_blocks_unapproved_higher_model(self,execute):
        forged=ModelJob.objects.create(charter=self.charter,owner=self.owner,prompt='x',requested_model='gpt-6-astra',requested_thinking='high')
        result=run_one()
        self.assertEqual(result.pk,forged.pk);self.assertEqual(result.state,'awaiting_approval')
        execute.assert_not_called()

    @patch('workspace.dispatcher.resolve_codex',return_value='codex.exe')
    @patch('workspace.codex_transport.execute')
    def test_approved_execution_records_new_usage_once(self,execute,binary):
        request=decide(self.proposal().pk,self.owner,True)
        self.source.execution_thread_id='original';self.source.save()
        TrackedSession.objects.create(external_id='original',name='original',project=self.project)
        def response(*args,**kwargs):
            self.assertEqual(args[2],'gpt-6.1-sol')
            kwargs['on_started']('01a190ed-1372-7741-a22c-9c4b996d3012')
            return {'completed':True,'text':'done','model':'gpt-6.1-sol','usage':{'input_tokens':200,'cached_input_tokens':50,'output_tokens':20,'reasoning_output_tokens':0}}
        execute.side_effect=response
        job=run_one();self.assertEqual(job.state,'completed')
        self.assertIsNone(run_one());self.assertEqual(execute.call_count,1)
        self.assertEqual(summarize(self.project)[1]['total'],330)
        request.refresh_from_db();self.assertEqual(request.source_usage['input_tokens'],100)
        self.assertEqual(request.approved_job.token_usage['input_tokens'],200)
