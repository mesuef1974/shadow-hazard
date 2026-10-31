import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from workspace.models import Project, ModelPolicy, SessionCharter, ModelJob
from execution_context.models import SessionDispatch, SessionRoutingPolicy
from .models import TrackedSession, TokenSample
from .services import summarize
from .delegated import job_index


class SessionAccountingTests(TestCase):
    def test_session_job_never_overrides_or_adds_to_collector_total(self):
        project=Project.objects.create(name='accounting')
        actor=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        policy=ModelPolicy.objects.create(category='review',title='review')
        charter=SessionCharter.objects.create(key='review',name='review',project=project,policy=policy,thread_id=str(uuid.uuid4()))
        session=TrackedSession.objects.create(project=project,external_id=charter.thread_id,name='review')
        TokenSample.objects.create(session=session,source_id=1,sampled_at=timezone.now(),model='gpt-6-luna',total=100,input=90,output=10,cached=20,reasoning=0,context=100,context_limit=1000)
        job=ModelJob.objects.create(charter=charter,owner=actor,prompt='test',requested_model='gpt-6-luna',requested_thinking='low',execution_thread_id=charter.thread_id,finished_at=timezone.now(),token_usage={'input_tokens':900,'output_tokens':100,'cached_input_tokens':0,'reasoning_output_tokens':0})
        SessionDispatch.objects.create(job=job,dispatch_key=uuid.uuid4(),target_charter=charter,target_thread_id=charter.thread_id,category='review',routing_policy=SessionRoutingPolicy.objects.get(category='review'),requested_model='gpt-6-luna',requested_thinking='low',route_reason='test')
        self.assertNotIn(charter.thread_id,job_index(project))
        for _ in range(2):
            self.assertEqual(summarize(project)[1]['total'],100)
        self.assertEqual(TokenSample.objects.count(),1)
