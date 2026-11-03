import uuid
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock,Mock,patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase,SimpleTestCase,override_settings
from workspace.models import Project,ModelPolicy,SessionCharter
from workspace.dispatcher import enqueue,run_one
from .models import SessionDispatch,SessionDispatchTurn
from .owner_delivery import deliver_one
from .owner_tools import OwnerTools,OwnerToolsError,load_config
from .desktop_session_bridge import BridgeError


class OwnerDeliveryTests(TestCase):
    def setUp(self):
        self.project=Project.objects.create(name='test')
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        policy=ModelPolicy.objects.create(category='backend',title='test')
        self.caller=SessionCharter.objects.create(key='backend',name='source',project=self.project,policy=policy,thread_id=str(uuid.uuid4()))
        self.target=SessionCharter.objects.create(key='review',name='target',project=self.project,policy=policy,thread_id=str(uuid.uuid4()))
        self.job=enqueue(self.target,self.owner,'authorized test',mode='workspace-write',task_kind='text')
        run_one();self.dispatch=SessionDispatch.objects.get(job=self.job)
        self.config={'server':'official/server.mjs','project_id':self.project.pk,'authorizing_user_id':self.owner.pk,
            'caller_thread_id':self.caller.thread_id,'server_sha256':'a'*64}
        self.reads=0;self.sends=0

    def invoke(self,mode='success'):
        def call(name,args,**ids):
            uuid.UUID(ids['call_id']);uuid.UUID(ids['context_id'])
            if name=='read_thread':
                self.reads+=1
                if self.reads==2:
                    if mode=='revoke_active':
                        self.owner.is_active=False;self.owner.save(update_fields=['is_active'])
                    elif mode=='replace_owner':
                        student=get_user_model().objects.create_user(username='saud',role='student')
                        type(self.job).objects.filter(pk=self.job.pk).update(owner=student)
                    elif mode=='revoke_policy':
                        type(self.target.policy).objects.filter(pk=self.target.policy_id).update(enabled=False)
                    elif mode=='revoke_model_ceiling':
                        SessionCharter.objects.filter(pk=self.target.pk).update(allowed_model_profiles=[])
                    elif mode=='replace_claim':
                        SessionDispatch.objects.filter(pk=self.dispatch.pk).update(claim_token=uuid.uuid4())
                    elif mode=='change_context':
                        self.job.task_context.instructions='changed after claim';self.job.task_context.save(update_fields=['instructions'])
                active=mode=='busy' or (mode=='became_busy' and self.reads==2)
                return {'thread':{'id':'wrong' if mode=='wrong_identity' else self.target.thread_id,
                    'kind':'codex','hostId':'local','cwd':str(settings.BASE_DIR.parent),
                    'status':{'type':'active' if active else 'idle'}},'turns':[{'status':'completed'}]}
            self.sends+=1
            self.assertTrue(self.dispatch.events.filter(state='owner_call_intent').exists())
            self.assertEqual(args['model'],self.dispatch.requested_model)
            self.assertEqual(args['thinking'],self.dispatch.requested_thinking)
            if mode=='timeout':raise OwnerToolsError('timeout after write')
            return {'threadId':'wrong' if mode=='wrong_receipt' else self.target.thread_id}
        client=Mock();client.call.side_effect=call
        with patch('execution_context.owner_delivery.ensure_channel',side_effect=lambda value:value),patch('execution_context.owner_delivery.load_config',return_value=self.config),patch('execution_context.owner_delivery.OwnerTools') as factory:
            factory.return_value.__enter__.return_value=client
            return deliver_one()

    def test_one_intent_precedes_one_send_and_no_second_delivery(self):
        self.assertEqual(self.invoke(),self.dispatch.pk)
        self.dispatch.refresh_from_db()
        self.assertEqual(self.dispatch.state,'sent')
        self.assertIsNone(deliver_one())
        self.assertEqual(self.sends,1)
        self.assertEqual(self.dispatch.events.filter(state='owner_call_intent').count(),1)
        self.assertEqual(SessionDispatchTurn.objects.count(),0)

    def test_preclaim_failure_is_recorded_without_sending_or_leaking_error_text(self):
        with patch('execution_context.owner_delivery.call_command',side_effect=ValueError('private-value')):
            with self.assertRaises(BridgeError):
                self.invoke()
        self.dispatch.refresh_from_db()
        self.assertEqual(self.dispatch.state,'queued')
        self.assertIsNone(self.dispatch.claim_token)
        event=self.dispatch.events.get(state='owner_preclaim_error')
        self.assertEqual(event.detail,{'stage':'claim','error_type':'ValueError',
                                      'before_send':True,'no_send_intent':True})
        self.assertNotIn('private-value',json.dumps(event.detail))
        self.assertFalse(self.dispatch.events.filter(state='owner_call_intent').exists())
        self.assertEqual(self.sends,0)

    def test_preapproved_execution_profile_survives_claim_and_send_with_lower_routing_default(self):
        self.dispatch.state='completed';self.dispatch.save(update_fields=['state'])
        self.job.state='completed';self.job.save(update_fields=['state'])
        self.target.allowed_model_profiles=[{'model':'gpt-6-luna','thinking':'low'},
                                            {'model':'gpt-6.1-sol','thinking':'medium'}]
        self.target.save(update_fields=['allowed_model_profiles'])
        self.job=enqueue(self.target,self.owner,'Preapproved elevated task',mode='workspace-write',task_kind='text')
        self.job.requested_model='gpt-6.1-sol';self.job.requested_thinking='medium'
        self.job.save(update_fields=['requested_model','requested_thinking'])
        run_one();self.dispatch=SessionDispatch.objects.get(job=self.job)
        self.assertEqual(self.dispatch.routing_policy.model,'gpt-6-luna')
        self.assertEqual(self.invoke(),self.dispatch.pk)
        self.assertEqual(self.sends,1)

    def test_active_session_and_activity_race_do_not_send(self):
        self.assertIsNone(self.invoke('busy'));self.assertEqual(self.sends,0)
        self.dispatch.refresh_from_db();self.assertEqual(self.dispatch.state,'queued')
        self.assertFalse(self.dispatch.events.filter(state='owner_call_intent').exists())

    def test_busy_target_is_sent_once_when_idle_after_cooldown(self):
        from django.utils import timezone
        from datetime import timedelta
        self.assertIsNone(self.invoke('busy'))
        self.assertIsNone(self.invoke())
        self.assertEqual(self.sends,0)
        self.dispatch.events.filter(state='deferred_busy').update(created_at=timezone.now()-timedelta(seconds=31))
        self.reads=0
        self.assertEqual(self.invoke(),self.dispatch.pk)
        self.assertIsNone(deliver_one())
        self.assertEqual(self.sends,1)
        self.assertEqual(self.dispatch.events.filter(state='owner_call_intent').count(),1)

    def test_activity_after_claim_releases_without_send_then_rechecks(self):
        self.assertIsNone(self.invoke('became_busy'))
        self.dispatch.refresh_from_db();self.job.refresh_from_db()
        self.assertEqual(self.dispatch.state,'queued')
        self.assertIsNone(self.dispatch.claim_token)
        self.assertEqual(self.job.state,'waiting_delivery')
        self.assertEqual(self.sends,0)
        self.reads=0
        self.assertEqual(self.invoke(),self.dispatch.pk)
        self.assertEqual(self.sends,1)

    def test_startup_recovers_only_idle_claim_with_no_possible_send(self):
        from django.utils import timezone
        from .owner_delivery import recover_unstarted_claims
        d=self.dispatch;d.state='claimed';d.claim_token=uuid.uuid4();d.claimed_by='codex-owner-mcp';d.claimed_at=timezone.now();d.save()
        client=Mock();factory=MagicMock();factory.return_value.__enter__.return_value=client
        with patch('execution_context.owner_delivery.load_config',return_value=self.config),patch(
                'execution_context.owner_delivery.ensure_channel',side_effect=lambda c:c),patch(
                'execution_context.owner_delivery.OwnerTools',factory),patch(
                'execution_context.owner_delivery._idle',return_value=False):
            self.assertEqual(recover_unstarted_claims(),[])
        d.refresh_from_db();self.assertEqual(d.state,'claimed')
        with patch('execution_context.owner_delivery.load_config',return_value=self.config),patch(
                'execution_context.owner_delivery.ensure_channel',side_effect=lambda c:c),patch(
                'execution_context.owner_delivery.OwnerTools',factory),patch(
                'execution_context.owner_delivery._idle',return_value=True):
            self.assertEqual(recover_unstarted_claims(),[d.pk])
        d.refresh_from_db();self.assertEqual(d.state,'queued')
        client.call.assert_not_called()

    def test_startup_never_releases_claim_after_durable_send_intent(self):
        from .owner_delivery import recover_unstarted_claims
        d=self.dispatch;d.state='claimed';d.claim_token=uuid.uuid4();d.claimed_by='codex-owner-mcp';d.save()
        d.events.create(state='owner_call_intent',detail={'call_id':'possibly sent'})
        with patch('execution_context.owner_delivery.load_config',return_value=self.config),patch(
                'execution_context.owner_delivery.ensure_channel',side_effect=lambda c:c),patch(
                'execution_context.owner_delivery.OwnerTools') as factory:
            self.assertEqual(recover_unstarted_claims(),[])
            factory.return_value.__enter__.return_value.call.assert_not_called()
        d.refresh_from_db();self.assertEqual(d.state,'claimed')

    def test_busy_oldest_session_does_not_block_next_idle_session(self):
        other=SessionCharter.objects.create(key='frontend',name='frontend',project=self.project,
            policy=self.target.policy,thread_id=str(uuid.uuid4()))
        other_job=enqueue(other,self.owner,'independent session task',mode='workspace-write',task_kind='text')
        run_one();other_dispatch=SessionDispatch.objects.get(job=other_job)
        client=Mock()
        client.call.return_value={'threadId':other.thread_id}
        factory=MagicMock();factory.return_value.__enter__.return_value=client
        with patch('execution_context.owner_delivery.load_config',return_value=self.config),patch(
                'execution_context.owner_delivery.ensure_channel',side_effect=lambda value:value),patch(
                'execution_context.owner_delivery.OwnerTools',factory),patch(
                'execution_context.owner_delivery._idle',side_effect=[False,True,True]) as idle:
            result=deliver_one()
        self.assertEqual(result,other_dispatch.pk)
        self.assertEqual(idle.call_count,3)
        self.assertEqual(factory.call_count,2)
        self.dispatch.refresh_from_db();other_dispatch.refresh_from_db()
        self.assertEqual(self.dispatch.state,'queued')
        self.assertEqual(other_dispatch.state,'sent')
        self.assertTrue(self.dispatch.events.filter(state='deferred_busy',detail__reason='target_active_before_claim').exists())
        calls=[call for call in client.call.call_args_list if call.args[0]=='send_message_to_thread']
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].args[1]['threadId'],other.thread_id)

    def test_ambiguous_send_is_quarantined_and_never_replayed(self):
        with self.assertRaises(BridgeError):self.invoke('timeout')
        self.dispatch.refresh_from_db();self.job.refresh_from_db()
        self.assertEqual(self.dispatch.state,'uncertain');self.assertEqual(self.job.state,'uncertain')
        self.assertIsNone(deliver_one());self.assertEqual(self.sends,1)

    def test_wrong_receipt_is_uncertain(self):
        with self.assertRaises(BridgeError):self.invoke('wrong_receipt')
        self.dispatch.refresh_from_db();self.assertEqual(self.dispatch.state,'uncertain')

    def test_wrong_host_identity_never_claims_or_sends(self):
        with self.assertRaises(BridgeError):self.invoke('wrong_identity')
        self.dispatch.refresh_from_db();self.assertEqual(self.dispatch.state,'queued');self.assertEqual(self.sends,0)

    def test_revoked_supervisor_is_rejected(self):
        self.owner.is_active=False;self.owner.save(update_fields=['is_active'])
        with self.assertRaises(BridgeError):self.invoke()
        self.assertEqual(self.sends,0)

    def test_unavailable_owner_channel_never_claims_or_sends(self):
        with patch('execution_context.owner_delivery.load_config',return_value=self.config),patch(
                'execution_context.owner_delivery.OwnerTools',side_effect=OwnerToolsError('channel closed')):
            with self.assertRaises(BridgeError):deliver_one()
        self.dispatch.refresh_from_db()
        self.assertEqual(self.dispatch.state,'queued')
        self.assertFalse(self.dispatch.events.filter(state='owner_call_intent').exists())

    def test_revocation_during_second_idle_check_blocks_before_intent(self):
        with self.assertRaises(BridgeError):self.invoke('revoke_active')
        self.assertEqual(self.sends,0)
        self.assertFalse(self.dispatch.events.filter(state='owner_call_intent').exists())

    def test_owner_role_replacement_during_idle_check_cannot_send(self):
        with self.assertRaises(BridgeError):self.invoke('replace_owner')
        self.assertEqual(self.sends,0)

    def test_policy_revocation_during_idle_check_cannot_send(self):
        with self.assertRaises(BridgeError):self.invoke('revoke_policy')
        self.assertEqual(self.sends,0)

    def test_model_ceiling_revocation_before_send_intent_blocks_delivery(self):
        with self.assertRaises(BridgeError):self.invoke('revoke_model_ceiling')
        self.assertEqual(self.sends,0)
        self.assertFalse(self.dispatch.events.filter(state='owner_call_intent').exists())

    def test_changed_claim_is_preserved_without_send_or_reset(self):
        with self.assertRaises(BridgeError):self.invoke('replace_claim')
        self.assertEqual(self.sends,0)
        self.dispatch.refresh_from_db()
        self.assertEqual(self.dispatch.state,'claimed')
        self.assertIsNotNone(self.dispatch.claim_token)

    def test_changed_context_during_idle_check_cannot_send(self):
        with self.assertRaises(BridgeError):self.invoke('change_context')
        self.assertEqual(self.sends,0)

    def completed_proof(self):
        import os
        from django.utils import timezone
        from workspace.models import DispatcherRun,DispatcherHeartbeat
        from workspace.memory import document
        from .models import SessionDispatchEvent
        now=timezone.now();usage={'input_tokens':10,'cached_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0,'total_tokens':11}
        self.job.state='completed';self.job.observed_model=self.dispatch.requested_model
        self.job.token_usage=usage;self.job.result='done';self.job.finished_at=now;self.job.save()
        d=self.dispatch;d.state='completed';d.turn_key='turn';d.claimed_at=now;d.completed_at=now;d.save()
        SessionDispatchTurn.objects.create(dispatch=d,target_thread_id=d.target_thread_id,turn_key='turn',
            model=d.requested_model,thinking=d.requested_thinking,usage=usage)
        SessionDispatchEvent.objects.create(dispatch=d,state='owner_call_accepted',detail={})
        receipt=document(self.project,self.target.key,'closure-proof','done',{'turn_id':'turn','usage':usage})
        SessionDispatchEvent.objects.create(dispatch=d,state='documentation_verified',detail={'receipt':receipt.pk,'digest':receipt.digest})
        DispatcherRun.objects.create(pid=os.getpid())
        DispatcherHeartbeat.objects.update_or_create(key='local',defaults={'pid':os.getpid(),'state':'running'})
        return {'turn_id':'turn','model':d.requested_model,'thinking':d.requested_thinking,
            'usage':usage,'result':'done','evidence':{'sha256':'proof-hash'}}

    def test_worker_rechecks_existing_closure_twice_without_sending_or_new_samples(self):
        from .owner_closure import verify_owner_closures
        from token_usage.models import TokenSample
        proof=self.completed_proof()
        with patch('execution_context.rollout_usage.find_rollout',return_value=Path('proof')),patch(
                'execution_context.rollout_usage.read_turn_evidence',return_value=proof) as reader:
            self.assertEqual(verify_owner_closures()['checked'],1)
            self.assertEqual(verify_owner_closures()['checked'],1)
            self.assertEqual(verify_owner_closures()['checked'],0)
        self.assertEqual(reader.call_count,2)
        self.assertEqual(self.dispatch.events.filter(state='owner_close_checked').count(),2)
        self.assertEqual(self.dispatch.turns.count(),1)
        self.assertEqual(TokenSample.objects.count(),0)

    def test_worker_rejects_changed_execution_evidence_without_changing_history(self):
        from .owner_closure import verify_owner_closures
        proof=self.completed_proof();proof['result']='different'
        with patch('execution_context.rollout_usage.find_rollout',return_value=Path('proof')),patch(
                'execution_context.rollout_usage.read_turn_evidence',return_value=proof):
            result=verify_owner_closures()
            self.assertEqual(len(result['rejected']),1)
            self.assertEqual(verify_owner_closures()['rejected'],result['rejected'])
        self.assertFalse(self.dispatch.events.filter(state='owner_close_checked').exists())
        self.assertEqual(self.dispatch.events.filter(state='owner_close_rejected').count(),1)
        self.job.refresh_from_db();self.assertEqual(self.job.result,'done')

    def test_worker_recovers_old_rejection_only_after_strict_proof_recheck(self):
        from datetime import timedelta
        from django.utils import timezone
        from .owner_closure import verify_owner_closures
        from token_usage.models import TokenSample
        proof=self.completed_proof()
        with patch('execution_context.rollout_usage.find_rollout',side_effect=ValueError('temporarily missing')):
            rejection=verify_owner_closures()['rejected'][0]
        # The cooldown suppresses immediate rechecks.
        with patch('execution_context.rollout_usage.find_rollout') as finder:
            self.assertEqual(verify_owner_closures()['rejected'],[rejection])
            finder.assert_not_called()
        self.dispatch.events.filter(pk=rejection).update(created_at=timezone.now()-timedelta(minutes=2))
        with patch('execution_context.rollout_usage.find_rollout',return_value=Path('proof')),patch(
                'execution_context.rollout_usage.read_turn_evidence',return_value=proof):
            result=verify_owner_closures()
        self.assertEqual(result,{'checked':1,'rejected':[]})
        self.assertTrue(self.dispatch.events.filter(pk=rejection,state='owner_close_rejected').exists())
        self.assertEqual(self.dispatch.turns.count(),1)
        self.assertEqual(TokenSample.objects.count(),0)

    def test_old_rejected_proof_does_not_block_an_unrelated_queued_delivery(self):
        import io
        from contextlib import nullcontext
        from django.core.management import call_command
        from workspace.models import DispatcherHeartbeat
        proof=self.completed_proof();proof['result']='tampered old result'
        other=enqueue(self.target,self.owner,'separate authorized task',mode='workspace-write',task_kind='text')
        run_one();queued=SessionDispatch.objects.get(job=other)
        def deliver():
            SessionDispatch.objects.filter(pk=queued.pk,state='queued').update(state='sent')
            return queued.pk
        with patch('execution_context.rollout_usage.find_rollout',return_value=Path('proof')),patch(
                'execution_context.rollout_usage.read_turn_evidence',return_value=proof),patch(
                'workspace.management.commands.run_model_dispatcher.single_worker',return_value=nullcontext(True)),patch(
                'workspace.management.commands.run_model_dispatcher.DispatcherWakeup'),patch(
                'workspace.management.commands.run_model_dispatcher.run_one',return_value=None),patch(
                'execution_context.readonly_service.run_pending',return_value=None),patch(
                'workspace.management.commands.run_model_dispatcher.observe_arrivals'),patch(
                'workspace.management.commands.run_model_dispatcher.reconcile'),patch(
                'workspace.management.commands.run_model_dispatcher.deliver_one',side_effect=deliver) as sender:
            call_command('run_model_dispatcher',stdout=io.StringIO())
        sender.assert_called_once()
        queued.refresh_from_db();self.assertEqual(queued.state,'sent')
        self.assertEqual(self.dispatch.events.filter(state='owner_close_rejected').count(),1)
        self.assertIn('حرج',DispatcherHeartbeat.objects.get(key='session_bridge').last_error)


class OwnerMcpTests(SimpleTestCase):
    def test_unloaded_without_turn_evidence_is_not_proven_idle(self):
        from .owner_delivery import _idle
        client=Mock()
        thread={'id':'target','kind':'codex','hostId':'local','cwd':str(settings.BASE_DIR.parent),
                'status':{'type':'notLoaded'}}
        client.call.return_value={'thread':thread,'turns':[]}
        self.assertFalse(_idle(client,'target','probe'))
        client.call.return_value={'thread':thread,'turns':[{'status':'inProgress'}]}
        self.assertFalse(_idle(client,'target','probe'))
        client.call.return_value={'thread':thread,'turns':[{'status':'completed'}]}
        self.assertTrue(_idle(client,'target','probe'))
        thread['status']['activeFlags']=['running']
        self.assertFalse(_idle(client,'target','probe'))

    def test_changed_server_fingerprint_fails_closed_before_launch(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(BASE_DIR=Path(folder)):
            root=Path(folder);(root/'.local').mkdir()
            server=root/'server.mjs';server.write_text('pinned official source')
            config={'enabled':True,'server':str(server),'node':sys.executable,
                'server_sha256':hashlib.sha256(server.read_bytes()).hexdigest(),'pipe':r'\\.\pipe\test-owner',
                'caller_thread_id':str(uuid.uuid4()),'project_id':1,'authorizing_user_id':1}
            (root/'.local/owner-connector.json').write_text(json.dumps(config))
            self.assertTrue(load_config()['enabled'])
            server.write_text('changed version')
            with self.assertRaises(OwnerToolsError):load_config()

    def test_call_metadata_is_fresh_executor_context_not_saved_turn_credentials(self):
        client=OwnerTools.__new__(OwnerTools);client.config={'caller_thread_id':'caller'}
        client.tools={'read_thread':{}}
        client.rpc=Mock(return_value={'content':[{'type':'text','text':'{"thread":{}}'}]})
        client.call('read_thread',{'threadId':'target'},call_id='fresh-call',context_id='fresh-context')
        meta=client.rpc.call_args.args[1]['_meta']
        self.assertEqual(meta,{'codexThreadId':'caller','codexTurnId':'fresh-context','codexCallId':'fresh-call'})

    def test_mcp_tool_error_is_not_treated_as_send_receipt(self):
        client=OwnerTools.__new__(OwnerTools);client.config={'caller_thread_id':'caller'}
        client.tools={'send_message_to_thread':{}};client.rpc=Mock(return_value={'isError':True,'content':[]})
        with self.assertRaises(OwnerToolsError):client.call('send_message_to_thread',{},call_id='call',context_id='context')
