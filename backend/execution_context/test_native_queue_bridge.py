from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from .native_queue_bridge import deliver_one, record_policy_mismatch
from .desktop_session_bridge import BridgeError


class NativeQueueTests(SimpleTestCase):
    def test_finalization_reports_actual_transport_or_explicit_legacy_unknown(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from .native_queue_bridge import finalize_verified
        for source,expected in [('codex-owner-mcp','codex-owner-mcp'),(None,'legacy-unspecified')]:
            with self.subTest(source=source):
                events=Mock()
                events.filter.return_value.order_by.return_value.first.return_value=SimpleNamespace(detail={'transport':source}) if source else None
                dispatch=SimpleNamespace(pk=1,claim_token='token',state='acknowledged',events=events)
                with patch('workspace.models.DispatcherRun.objects.filter') as runs,patch('execution_context.native_queue_bridge._command') as command:
                    runs.return_value.order_by.return_value.first.return_value=SimpleNamespace(pk=99)
                    self.assertTrue(finalize_verified(dispatch,{'turn_id':'turn','result':'done'}))
                    self.assertEqual(command.call_args.kwargs['detail']['transport'],expected)
                    self.assertEqual(command.call_args.kwargs['detail']['dispatcher_run_id'],99)

    def invoke(self, result=None, error=None):
        with patch('execution_context.delivery.deliver_one', return_value=result, side_effect=error) as controlled, patch('execution_context.desktop_session_bridge.AppServer') as queue:
            if error:
                with self.assertRaises(BridgeError):
                    deliver_one()
            else:
                self.assertEqual(deliver_one(), result)
            controlled.assert_called_once_with()
            queue.assert_not_called()

    def test_controlled_delivery_only(self):
        self.invoke(7)

    def test_busy_session_is_deferred_without_queue(self):
        self.invoke(None)

    def test_control_failure_never_falls_back_to_queue(self):
        self.invoke(error=BridgeError('active writer'))


class ReviewStateTests(TestCase):
    def test_mismatch_finishes_transport_without_acceptance_and_is_idempotent(self):
        import uuid
        from django.contrib.auth import get_user_model
        from workspace.models import Project, ModelPolicy, SessionCharter, ModelJob
        from .models import SessionDispatch, SessionDispatchTurn
        from token_usage.models import TokenSample
        project=Project.objects.create(name='test')
        owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        policy=ModelPolicy.objects.create(category='review-test',title='test')
        charter=SessionCharter.objects.create(key='review-test',project=project,policy=policy,name='test')
        job=ModelJob.objects.create(charter=charter,owner=owner,prompt='test',state='running',requested_model='gpt-6-luna',requested_thinking='low')
        dispatch=SessionDispatch.objects.create(job=job,dispatch_key=uuid.uuid4(),target_charter=charter,target_thread_id='thread',category='review',requested_model='gpt-6-luna',requested_thinking='low',state='acknowledged')
        usage={'input_tokens':10,'cached_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0,'total_tokens':11}
        evidence={'turn_id':'turn','model':'gpt-6.1-sol','thinking':'low','usage':usage,'evidence':{'source':'test'},'result':'done'}
        record_policy_mismatch(dispatch,evidence)
        record_policy_mismatch(dispatch,evidence)
        job.refresh_from_db(); dispatch.refresh_from_db()
        self.assertEqual(dispatch.state,'finished_review')
        self.assertIsNotNone(dispatch.completed_at)
        self.assertEqual(job.state,'needs_review')
        self.assertEqual(SessionDispatchTurn.objects.count(),1)
        self.assertEqual(TokenSample.objects.count(),0)
        self.assertEqual(dispatch.events.filter(state='execution_finished').count(),1)
