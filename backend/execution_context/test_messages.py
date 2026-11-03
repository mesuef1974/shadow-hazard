import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from workspace.models import Project,ModelPolicy,SessionCharter,ModelJob
from workspace.dispatcher import enqueue,run_one
from .messages import send_message
from .models import SessionMessage,SessionDispatch,SessionDispatchTurn


class MessageTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.project=Project.objects.create(name='test')
        self.policy=ModelPolicy.objects.create(category='coordination',title='test')
        self.source=SessionCharter.objects.create(key='coordination',name='source',project=self.project,policy=self.policy,thread_id=str(uuid.uuid4()))
        self.target=SessionCharter.objects.create(key='backend',name='target',project=self.project,policy=self.policy,thread_id=str(uuid.uuid4()))

    def send(self,**kwargs):
        args=dict(sender=self.source,recipient=self.target,owner=self.owner,body='message',message_key=uuid.uuid4())
        args.update(kwargs)
        return send_message(**args)

    def test_idempotency_and_single_existing_outbox(self):
        key=uuid.uuid4();m=self.send(message_key=key)
        self.assertEqual(self.send(message_key=key).pk,m.pk)
        self.assertEqual((ModelJob.objects.count(),SessionMessage.objects.count()),(1,1))
        run_one();m.refresh_from_db()
        self.assertEqual(m.delivery.target_charter_id,self.target.pk)
        self.assertEqual(m.state,'queued')
        self.assertIsNone(m.delivery.acknowledged_at)
        self.assertEqual(SessionDispatch.objects.count(),1)
        self.assertEqual(SessionDispatchTurn.objects.count(),0)
        with self.assertRaises(ValueError):self.send(message_key=key,body='different')
        with self.assertRaises(ProtectedError):m.job.delete()

    def test_message_body_is_trimmed_once_and_stored_normalized(self):
        message=self.send(body='  message with spaces  ')
        self.assertEqual(message.body,'message with spaces')
        self.assertEqual(message.job.prompt,'message with spaces')

    def test_reply_correlation_and_project_boundaries(self):
        original=self.send()
        reply=self.send(sender=self.target,recipient=self.source,kind='reply',reply_to=original)
        self.assertEqual(reply.correlation_key,original.correlation_key)
        self.assertEqual(reply.reply_to_id,original.pk)
        other=Project.objects.create(name='other')
        self.target.project=other;self.target.save()
        with self.assertRaises(ValueError):self.send()

    def test_permission_and_human_origin_registration(self):
        student=get_user_model().objects.create_user(username='saud',role='student')
        with self.assertRaises(ValueError):self.send(owner=student)
        job=enqueue(self.target,self.owner,'text',mode='workspace-write',task_kind='text')
        self.assertIsNone(job.session_message.sender_id)
        self.assertEqual(job.session_message.recipient_id,self.target.pk)
        self.assertEqual(job.session_message.state,'queued')
        self.assertNotIn('token_usage',[f.name for f in SessionMessage._meta.fields])

    def test_admin_is_read_only_and_requires_staff(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from .admin import SessionMessageAdmin
        message=self.send()
        request=RequestFactory().get('/')
        request.user=self.owner
        admin=SessionMessageAdmin(SessionMessage,AdminSite())
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request,message))
        self.assertFalse(admin.has_delete_permission(request,message))
        self.owner.is_staff=True;self.owner.is_superuser=True;self.owner.save()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/admin/execution_context/sessionmessage/').status_code,200)
        student=get_user_model().objects.create_user(username='saud',role='student')
        self.client.force_login(student)
        self.assertEqual(self.client.get('/admin/execution_context/sessionmessage/').status_code,302)

    def test_rollout_confirmation_is_idempotent_and_never_adds_token_samples(self):
        import json
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.management import call_command
        from workspace.test_dispatcher import write_test_rollout
        from token_usage.models import TokenSample
        from .native_queue_bridge import reconcile
        message=self.send();run_one()
        output=StringIO();call_command('session_outbox','claim',stdout=output)
        payload=json.loads(output.getvalue());dispatch=message.job.session_dispatch
        samples=TokenSample.objects.count()
        with TemporaryDirectory() as folder:
            root=Path(folder);sessions=root/'sessions';sessions.mkdir()
            path=write_test_rollout(sessions/f'rollout-{dispatch.target_thread_id}.jsonl',dispatch.target_thread_id,
                dispatch.requested_model,dispatch.requested_thinking,'message-turn',dispatch.claimed_at.timestamp())
            call_command('session_outbox','sent',dispatch=dispatch.pk,claim_token=payload['claim_token'],stdout=StringIO())
            with patch.dict('os.environ',{'CODEX_HOME':folder}):
                reconcile();reconcile()
        dispatch.refresh_from_db();message.job.refresh_from_db()
        self.assertEqual(dispatch.state,'completed')
        self.assertEqual(SessionDispatchTurn.objects.count(),1)
        self.assertEqual(message.job.token_usage['total_tokens'],15)
        self.assertEqual(TokenSample.objects.count(),samples)

    def test_mismatched_model_keeps_actual_usage_once_without_success(self):
        import json
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.management import call_command
        from workspace.test_dispatcher import write_test_rollout
        from token_usage.models import TokenSample
        from .native_queue_bridge import reconcile
        message=self.send();run_one();output=StringIO()
        call_command('session_outbox','claim',stdout=output)
        payload=json.loads(output.getvalue());dispatch=message.job.session_dispatch
        samples=TokenSample.objects.count()
        with TemporaryDirectory() as folder:
            sessions=Path(folder)/'sessions';sessions.mkdir()
            write_test_rollout(sessions/f'rollout-{dispatch.target_thread_id}.jsonl',dispatch.target_thread_id,
                'gpt-6-astra',dispatch.requested_thinking,'mismatched-turn',dispatch.claimed_at.timestamp())
            call_command('session_outbox','sent',dispatch=dispatch.pk,claim_token=payload['claim_token'],stdout=StringIO())
            with patch.dict('os.environ',{'CODEX_HOME':folder}):reconcile();reconcile()
        message.job.refresh_from_db();dispatch.refresh_from_db()
        self.assertEqual(message.job.state,'needs_review')
        self.assertEqual(message.job.observed_model,'gpt-6-astra')
        self.assertEqual(message.job.token_usage['total_tokens'],15)
        self.assertEqual(SessionDispatchTurn.objects.count(),1)
        self.assertIsNotNone(dispatch.completed_at)
        self.assertEqual(dispatch.state,'finished_review')
        self.assertEqual(TokenSample.objects.count(),samples)

    def test_counter_conflict_is_saved_unverified_without_adding_to_totals(self):
        import json
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.management import call_command
        from workspace.test_dispatcher import write_test_rollout
        from token_usage.models import TokenSample
        from .native_queue_bridge import reconcile
        from .models import SessionDispatchEvent
        from operations.services import task_snapshot
        message=self.send();run_one();output=StringIO()
        call_command('session_outbox','claim',stdout=output)
        payload=json.loads(output.getvalue());dispatch=message.job.session_dispatch
        samples=TokenSample.objects.count()
        with TemporaryDirectory() as folder:
            sessions=Path(folder)/'sessions';sessions.mkdir()
            path=write_test_rollout(sessions/f'rollout-{dispatch.target_thread_id}.jsonl',dispatch.target_thread_id,
                dispatch.requested_model,dispatch.requested_thinking,'conflicting-counts',dispatch.claimed_at.timestamp())
            rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            usage=next(r['payload']['turn_token_usage'] for r in rows if r['type']=='token_usage_record')
            usage['input_tokens']+=1;usage['total_tokens']+=1
            path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n',encoding='utf-8')
            call_command('session_outbox','sent',dispatch=dispatch.pk,claim_token=payload['claim_token'],stdout=StringIO())
            with patch.dict('os.environ',{'CODEX_HOME':folder}):reconcile();reconcile()
        message.job.refresh_from_db()
        self.assertEqual(message.job.state,'needs_review')
        self.assertEqual(message.job.token_usage,{})
        event=SessionDispatchEvent.objects.get(dispatch=dispatch,state='evidence_rejected')
        self.assertEqual(event.detail['unverified_turn_usage']['total_tokens'],16)
        self.assertIn('execution_evidence_rejected',task_snapshot(message.job)['violations'])
        self.assertEqual(SessionDispatchTurn.objects.count(),0)
        self.assertEqual(TokenSample.objects.count(),samples)

    def test_arrival_requires_user_marker_and_never_completes_task(self):
        import json
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.management import call_command
        from workspace.test_dispatcher import write_test_rollout
        from .native_queue_bridge import observe_arrivals
        from .models import SessionDispatchEvent
        message=self.send();run_one()
        output=StringIO();call_command('session_outbox','claim',stdout=output)
        payload=json.loads(output.getvalue());dispatch=message.job.session_dispatch
        with TemporaryDirectory() as folder:
            sessions=Path(folder)/'sessions';sessions.mkdir()
            path=write_test_rollout(sessions/f'rollout-{dispatch.target_thread_id}.jsonl',dispatch.target_thread_id,
                dispatch.requested_model,dispatch.requested_thinking,'arrival-turn',dispatch.claimed_at.timestamp())
            rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            rows=[r for r in rows if r.get('payload',{}).get('type')!='task_complete']
            incoming=next(r for r in rows if r.get('payload',{}).get('role')=='user')
            bound=next(r for r in rows if r.get('payload',{}).get('type')=='item_completed')
            incoming['payload']['role']='assistant'
            bound['payload']['item']['type']='AgentMessage'
            path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n',encoding='utf-8')
            call_command('session_outbox','sent',dispatch=dispatch.pk,claim_token=payload['claim_token'],stdout=StringIO())
            with patch.dict('os.environ',{'CODEX_HOME':folder}):
                observe_arrivals();dispatch.refresh_from_db();self.assertEqual(dispatch.state,'sent')
                incoming['payload']['role']='user'
                bound['payload']['item']['type']='UserMessage'
                bound['payload']['turn_id']='another-turn'
                path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n',encoding='utf-8')
                observe_arrivals();dispatch.refresh_from_db();self.assertEqual(dispatch.state,'sent')
                bound['payload']['turn_id']='arrival-turn'
                path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n',encoding='utf-8')
                observe_arrivals();observe_arrivals()
            dispatch.refresh_from_db();message.job.refresh_from_db()
            self.assertEqual(dispatch.state,'acknowledged')
            self.assertEqual(message.job.observed_model,dispatch.requested_model)
            self.assertEqual(SessionDispatchEvent.objects.filter(dispatch=dispatch,state='turn_started').count(),1)
            self.assertEqual(SessionDispatchTurn.objects.count(),0)

    def test_rejected_finalization_is_quarantined_once(self):
        from unittest.mock import patch
        from django.core.management.base import CommandError
        from .native_queue_bridge import finalize_verified
        from .models import SessionDispatchEvent
        message=self.send();run_one();dispatch=message.job.session_dispatch
        with patch('execution_context.native_queue_bridge._command',side_effect=CommandError('proof rejected')):
            for _ in range(2):self.assertFalse(finalize_verified(dispatch,{'turn_id':'turn','result':'result'}))
        message.job.refresh_from_db()
        self.assertEqual(message.job.state,'needs_review')
        self.assertEqual(SessionDispatchEvent.objects.filter(dispatch=dispatch,state='verification_failed').count(),1)
        self.assertEqual(SessionDispatchTurn.objects.count(),0)

    def test_retired_app_server_sender_leaves_request_queued(self):
        from unittest.mock import patch
        from .desktop_session_bridge import deliver_one, BridgeError
        message = self.send()
        run_one()
        with patch('execution_context.desktop_session_bridge.AppServer') as server:
            with self.assertRaises(BridgeError):
                deliver_one()
            server.assert_not_called()
        dispatch = message.job.session_dispatch
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.state, 'queued')
        self.assertIsNone(dispatch.claim_token)
        self.assertIsNone(dispatch.sent_at)
