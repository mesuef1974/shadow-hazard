import uuid
from unittest.mock import patch
from django.core.management import call_command, CommandError
from django.contrib.auth import get_user_model
from django.test import TestCase
from execution_context.models import SessionRoutingPolicy, SessionDispatch
from execution_context.readonly_service import run_pending
from .dispatcher import enqueue, run_one
from .models import Project, ModelPolicy, SessionCharter
from .model_limits import model_limit_error


class ModelCeilingTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.project=Project.objects.create(name='Model limits')
        self.policy=ModelPolicy.objects.create(category='text',title='Policy')
        self.charter=SessionCharter.objects.create(project=self.project,policy=self.policy,
            key='coordination',name='Coordinator',thread_id=str(uuid.uuid4()))

    def route(self,model='gpt-6.1-sol',thinking='high'):
        SessionRoutingPolicy.objects.update_or_create(category='text',
            defaults={'model':model,'thinking':thinking,'enabled':True})

    def test_routing_upgrade_is_blocked_after_final_selection_in_both_modes(self):
        for mode in ('read-only','workspace-write'):
            with self.subTest(mode=mode):
                job=enqueue(self.charter,self.owner,'Summarize this task',mode=mode,task_kind='text')
                self.route()
                result=run_one()
                self.assertEqual(result.pk,job.pk)
                self.assertEqual(result.state,'awaiting_approval')
                self.assertEqual(result.requested_model,'gpt-6.1-sol')
                self.assertIn('خارج سقف',result.outcome_note)
                self.assertFalse(SessionDispatch.objects.filter(job=job).exists())

    def test_preapproved_pair_needs_no_per_task_escalation(self):
        self.charter.allowed_model_profiles=[{'model':'gpt-6.1-sol','thinking':'high'}]
        self.charter.save(update_fields=['allowed_model_profiles'])
        job=enqueue(self.charter,self.owner,'Summarize this task',task_kind='text')
        self.route()
        with patch('execution_context.escalation.authorized',side_effect=AssertionError('per-task escalation')):
            result=run_one()
        self.assertEqual(result.state,'waiting_readonly')
        job.refresh_from_db()
        self.assertEqual(job.task_context.manifest['model_ceiling'],self.charter.allowed_model_profiles)

    def test_missing_malformed_or_unapproved_effort_is_denied(self):
        for profiles in ([],None,{},['gpt-6-luna'],[{'model':True,'thinking':'low'}]):
            with self.subTest(profiles=profiles):
                self.charter.allowed_model_profiles=profiles
                self.assertIsNotNone(model_limit_error(self.charter,'gpt-6-luna','low'))
        self.charter.allowed_model_profiles=[{'model':'gpt-6-luna','thinking':'low'}]
        self.assertIsNotNone(model_limit_error(self.charter,'gpt-6-luna','medium'))

    def test_ceiling_is_independent_even_when_charters_share_model_policy(self):
        other=SessionCharter.objects.create(project=self.project,policy=self.policy,key='review',name='Review')
        self.charter.allowed_model_profiles=[{'model':'gpt-6.1-sol','thinking':'high'}]
        self.assertIsNone(model_limit_error(self.charter,'gpt-6.1-sol','high'))
        self.assertIsNotNone(model_limit_error(other,'gpt-6.1-sol','high'))

    def test_sessions_show_ceiling_and_missing_ceiling_without_raw_json(self):
        self.client.force_login(self.owner)
        response=self.client.get('/sessions/')
        self.assertContains(response,'سقف النماذج المعتمد لهذا الميثاق')
        self.assertContains(response,'gpt-6-luna')
        self.charter.allowed_model_profiles=[]
        self.charter.save(update_fields=['allowed_model_profiles'])
        response=self.client.get('/sessions/')
        self.assertContains(response,'غير جاهز — لا يوجد سقف نماذج معتمد')

    def test_revocation_before_claim_rolls_back_claim_without_send_intent(self):
        job=enqueue(self.charter,self.owner,'Summarize this task',mode='workspace-write',task_kind='text')
        self.route('gpt-6-luna','low')
        run_one()
        self.charter.allowed_model_profiles=[]
        self.charter.save(update_fields=['allowed_model_profiles'])
        with self.assertRaises(CommandError):call_command('session_outbox','claim')
        dispatch=SessionDispatch.objects.get(job=job)
        self.assertEqual(dispatch.state,'queued')
        self.assertFalse(dispatch.events.filter(state='owner_call_intent').exists())

    @patch('execution_context.readonly_service.execute')
    def test_changed_routing_policy_during_wait_does_not_execute_old_profile(self,execute):
        from execution_context.readonly_transport import IsolationError
        job=enqueue(self.charter,self.owner,'Summarize this task',task_kind='text')
        self.route('gpt-6-luna','low')
        run_one()
        self.route('gpt-6-luna','medium')
        with self.assertRaises(IsolationError):run_pending(job.pk)
        execute.assert_not_called()

    @patch('execution_context.readonly_service.execute')
    def test_revocation_while_waiting_is_checked_before_isolated_execution(self,execute):
        job=enqueue(self.charter,self.owner,'Summarize this task',task_kind='text')
        self.route('gpt-6-luna','low')
        self.assertEqual(run_one().state,'waiting_readonly')
        self.charter.allowed_model_profiles=[]
        self.charter.save(update_fields=['allowed_model_profiles'])
        self.assertIsNone(run_pending(job.pk))
        execute.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.state,'awaiting_approval')
