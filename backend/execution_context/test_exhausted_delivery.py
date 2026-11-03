import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from workspace.models import ModelJob, ModelPolicy, Project, ProjectDocumentation, SessionCharter
from token_usage.models import TokenSample
from .models import SessionDispatch, SessionDispatchEvent, SessionDispatchTurn
from .exhausted_delivery import reconcile_exhausted
from .desktop_session_bridge import BridgeError


class ExhaustedDeliveryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create(username='mesuef', role='supervisor')
        project = Project.objects.create(name='test')
        charter = SessionCharter.objects.create(project=project,
            policy=ModelPolicy.objects.create(category='test'), key='frontend', name='test', thread_id=str(uuid.uuid4()))
        job = ModelJob.objects.create(charter=charter, owner=self.owner,
            state='needs_review', requested_model='gpt-6.1-sol', requested_thinking='high')
        self.dispatch = SessionDispatch.objects.create(job=job, target_charter=charter,
            target_thread_id=charter.thread_id, dispatch_key=uuid.uuid4(), state='uncertain',
            attempt_no=2, max_attempts=2, requested_model='gpt-6.1-sol', requested_thinking='high',
            claimed_at=timezone.now(), acknowledged_at=timezone.now())
        turn_id = str(uuid.uuid4())
        self.proof = {'turn_id':turn_id,'model':'gpt-6.1-sol','thinking':'high',
            'usage':{'input_tokens':10,'cached_input_tokens':5,'output_tokens':2,'total_tokens':12},
            'result':'ESCALATION_REQUEST: backend route unavailable', 'evidence':{'sha256':'a'*64}}
        SessionDispatchTurn.objects.create(dispatch=self.dispatch, target_thread_id=charter.thread_id,
            turn_key=turn_id, model=self.proof['model'], thinking='high', usage=self.proof['usage'])
        self.intent = SessionDispatchEvent.objects.create(dispatch=self.dispatch,state='owner_call_intent',
            detail={'call_id':str(uuid.uuid4()),'context_id':str(uuid.uuid4()),
                    'caller_thread_id':str(uuid.uuid4()),'attempt_no':2})
        SessionDispatchEvent.objects.create(dispatch=self.dispatch,state='owner_call_accepted',
            detail={'call_id':self.intent.detail['call_id']})
        SessionDispatchEvent.objects.create(dispatch=self.dispatch,state='stuck',detail={'turn_id':turn_id})
        self.cfg={'project_id':project.pk,'authorizing_user_id':self.owner.pk}

    def resolve(self):
        with patch('execution_context.owner_tools.load_config',return_value=self.cfg), \
             patch('execution_context.rollout_usage.find_rollout',return_value='fixture'), \
             patch('execution_context.rollout_usage.read_turn_evidence',return_value=self.proof) as read:
            result = reconcile_exhausted(self.dispatch.pk,self.owner)
        self.assertEqual(read.call_args.kwargs['expected_thinking'],'high')
        self.assertIn(':2]',read.call_args.kwargs['expected_marker'])
        return result

    def test_proved_exhaustion_is_terminal_but_task_is_blocked_without_extra_usage(self):
        before=TokenSample.objects.count()
        first=self.resolve();second=self.resolve()
        self.dispatch.refresh_from_db();self.dispatch.job.refresh_from_db()
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(self.dispatch.state,'finished_review')
        self.assertEqual(self.dispatch.job.state,'blocked')
        self.assertEqual(self.dispatch.job.result,self.proof['result'])
        self.assertEqual(self.dispatch.turns.count(),1)
        self.assertEqual(TokenSample.objects.count(),before)
        self.assertEqual(self.dispatch.events.filter(state='exhaustion_verified').count(),1)

    def test_mismatched_usage_preserves_uncertain_history(self):
        self.proof['usage']={'total_tokens':999}
        with self.assertRaises(BridgeError):self.resolve()
        self.dispatch.refresh_from_db()
        self.assertEqual(self.dispatch.state,'uncertain')
        self.assertFalse(ProjectDocumentation.objects.exists())

    def test_missing_acceptance_or_remaining_attempt_cannot_close(self):
        self.dispatch.events.filter(state='owner_call_accepted').delete()
        with self.assertRaises(BridgeError):self.resolve()
        self.assertFalse(ProjectDocumentation.objects.exists())

    def test_other_supervisor_cannot_resolve(self):
        self.cfg['authorizing_user_id']=-1
        with self.assertRaises(PermissionDenied):self.resolve()
