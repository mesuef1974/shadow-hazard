import hashlib
import uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,TransactionTestCase
from workspace.models import Project,ModelPolicy,SessionCharter,ModelJob
from workspace.memory import document
from execution_context.models import SessionRoutingPolicy
from .models import OperatorMonitorPolicy,OperatorIncident
from .operator_monitor import inspect_policy,SCOPES


class OperatorMonitorTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create(username='mesuef',role='supervisor')
        self.project=Project.objects.create(name='monitor-test')
        model=ModelPolicy.objects.create(category='operator',max_attempts=1)
        self.caller=SessionCharter.objects.create(project=self.project,policy=model,key='coordination',thread_id=str(uuid.uuid4()),state='active')
        self.operator=SessionCharter.objects.create(project=self.project,policy=model,key='session_operator',thread_id=str(uuid.uuid4()),state='active')
        self.policy=OperatorMonitorPolicy.objects.create(project=self.project,charter=self.operator,owner=self.owner,enabled=True,scopes=SCOPES)
        SessionRoutingPolicy.objects.update_or_create(category='text',defaults={'enabled':True,'model':'gpt-6-luna','thinking':'low','max_attempts':1,'max_escalations':0})
        self.config={'project_id':self.project.pk,'authorizing_user_id':self.owner.pk,'caller_thread_id':self.caller.thread_id,'server_sha256':'server','owner_identity':{'sha256':'image'}}
        e={'status':'operator_monitor_activation_authorized','policy_id':self.policy.pk,'charter_id':self.operator.pk,
            'owner_id':self.owner.pk,'scopes':SCOPES,'cooldown_seconds':300,
            'charter_sha256':hashlib.sha256((self.operator.scope+'\n'+self.operator.rules).encode()).hexdigest(),
            'owner_channel':{'caller_thread_id':self.caller.thread_id,'server_sha256':'server','owner_image_sha256':'image'}}
        self.policy.activation_receipt=document(self.project,self.operator.key,'activate','تفعيل تجريبي',e)
        self.policy.save()

    def cycle(self,conditions=None):
        with patch('operations.operator_monitor.load_config',return_value=self.config),patch('operations.operator_monitor.conditions',return_value=conditions or {}):
            return inspect_policy(self.policy.pk)

    def fault(self):return {'dispatch:1:uncertain':('delivery_uncertain',{'dispatch_id':1})}

    def test_healthy_zero_jobs(self):
        self.assertEqual(self.cycle(),0);self.assertEqual(ModelJob.objects.count(),0)

    def test_dedup_repeated_cycle_and_message_context(self):
        self.assertEqual(self.cycle(self.fault()),1);self.assertEqual(self.cycle(self.fault()),0)
        job=ModelJob.objects.get();self.assertEqual(job.task_context.kind,'text')
        self.assertIn('project_memory inspect',job.prompt)
        self.assertIn('project_memory document',job.prompt)
        self.assertIn('--compact --document-ids',job.prompt)
        self.assertIn('reread_verified=true',job.prompt)
        self.assertIn('لا اكتشاف أدوات',job.prompt)
        from .operator_monitor import LOCAL_DOCUMENTATION
        self.assertLess(len(LOCAL_DOCUMENTATION),751)
        self.assertNotIn('ثم أعد قراءة الإيصال الناتج بأمر inspect',job.prompt)
        self.assertEqual(job.task_context.skills,[]);self.assertEqual(job.task_context.plugins,[])
        self.assertEqual(job.mode,'workspace-write');self.assertEqual(job.requested_model,'gpt-6-luna')
        self.assertEqual(job.session_message.sender,self.caller)
        self.assertEqual(OperatorIncident.objects.count(),1)

    def test_changed_condition_resolves_and_reopens_new_generation(self):
        self.cycle(self.fault());self.cycle()
        self.assertEqual(OperatorIncident.objects.get().state,'resolved')
        self.cycle(self.fault())
        self.assertEqual(list(OperatorIncident.objects.order_by('generation').values_list('generation',flat=True)),[1,2])
        self.assertEqual(ModelJob.objects.count(),1)

    def test_busy_existing_batch_defers_new_condition(self):
        self.cycle(self.fault())
        self.cycle({'dispatch:2:uncertain':('delivery_uncertain',{'dispatch_id':2})})
        self.assertEqual(ModelJob.objects.count(),1)
        self.assertEqual(OperatorIncident.objects.filter(state='open',job=None).count(),1)

    def test_disabled_revoked_bad_activation(self):
        self.policy.enabled=False;self.policy.save();self.assertEqual(self.cycle(self.fault()),0)
        self.policy.enabled=True;self.policy.save();self.owner.is_active=False;self.owner.save()
        self.assertEqual(self.cycle(self.fault()),0)
        self.owner.is_active=True;self.owner.save()
        self.policy.activation_receipt.evidence={};self.policy.activation_receipt.save()
        self.assertEqual(self.cycle(self.fault()),0);self.assertEqual(ModelJob.objects.count(),0)

    def test_self_send_fails_closed(self):
        self.operator.thread_id=self.caller.thread_id;self.operator.save()
        self.assertEqual(self.cycle(self.fault()),0)
        self.policy.refresh_from_db();self.assertEqual(self.policy.last_error,'safe_caller_transfer_required')

    def test_self_notification_requires_matching_official_completed_delivery(self):
        from .operator_monitor import self_notification_allowed
        from execution_context.models import SessionDispatch,SessionDispatchEvent,SessionDispatchTurn,SessionMessage
        channel={**self.config,'caller_thread_id':self.operator.thread_id}
        usage={'input_tokens':10,'cached_input_tokens':5,'output_tokens':2,'total_tokens':12}
        job=ModelJob.objects.create(charter=self.operator,owner=self.owner,state='completed',
            mode='workspace-write',token_usage=usage,result='ready')
        dispatch=SessionDispatch.objects.create(job=job,dispatch_key=uuid.uuid4(),target_charter=self.operator,
            target_thread_id=self.operator.thread_id,state='completed',turn_key='turn')
        SessionDispatchTurn.objects.create(dispatch=dispatch,target_thread_id=self.operator.thread_id,
            turn_key='turn',model='gpt-6-luna',thinking='low',usage=usage)
        SessionMessage.objects.create(job=job,sender=self.operator,recipient=self.operator,
            message_key=uuid.uuid4(),correlation_key=uuid.uuid4(),body='ack')
        SessionDispatchEvent.objects.create(dispatch=dispatch,state='owner_call_intent',detail={
            'caller_thread_id':self.operator.thread_id,'call_id':'call'})
        SessionDispatchEvent.objects.create(dispatch=dispatch,state='owner_call_accepted',detail={'call_id':'call'})
        completion=document(self.project,self.operator.key,'completion','ready',{'turn_id':'turn','usage':usage})
        SessionDispatchEvent.objects.create(dispatch=dispatch,state='documentation_verified',
            detail={'receipt':completion.pk,'digest':completion.digest})
        owner_channel={**self.policy.activation_receipt.evidence['owner_channel'],
            'caller_thread_id':self.operator.thread_id}
        proof=document(self.project,self.operator.key,'self-proof','proved self delivery',{
            'status':'operator_idle_self_notification_verified','owner_id':self.owner.pk,'owner_channel':owner_channel,
            'dispatch_id':dispatch.pk,'turn_id':'turn','completion_receipt_id':completion.pk})
        evidence={**self.policy.activation_receipt.evidence,'owner_channel':owner_channel,
            'allow_idle_self_notification':True,'self_notification_proof_id':proof.pk,
            'self_notification_proof_digest':proof.digest}
        self.policy.activation_receipt=document(self.project,self.operator.key,'self-activation','approved',evidence)
        self.assertTrue(self_notification_allowed(self.policy,channel))
        for version in (1,2):
            previous=self.policy.activation_receipt
            evidence={**previous.evidence,'owner_channel':{**owner_channel,'server_sha256':f'server-{version}',
                'owner_image_sha256':f'image-{version}'},'runtime_refresh':{
                    'status':'same_owner_runtime_refresh_authorized','prior_activation_id':previous.pk,
                    'prior_activation_digest':previous.digest}}
            self.policy.activation_receipt=document(self.project,self.operator.key,f'runtime-{version}','approved runtime refresh',evidence)
            channel={**channel,'server_sha256':f'server-{version}','owner_identity':{'sha256':f'image-{version}'}}
            self.assertTrue(self_notification_allowed(self.policy,channel))
        dispatch.state='uncertain';dispatch.save(update_fields=['state'])
        self.assertFalse(self_notification_allowed(self.policy,channel))

    def test_runtime_refresh_cannot_change_authority_or_reference_unverified_approval(self):
        from .operator_monitor import _notification_proof_channel
        previous=self.policy.activation_receipt
        evidence={**previous.evidence,'owner_channel':{**previous.evidence['owner_channel'],
            'server_sha256':'new-server','owner_image_sha256':'new-image'},'runtime_refresh':{
                'status':'same_owner_runtime_refresh_authorized','prior_activation_id':previous.pk,
                'prior_activation_digest':previous.digest}}
        self.assertEqual(_notification_proof_channel(self.policy,evidence),previous.evidence['owner_channel'])
        for changed in [
            {**evidence,'scopes':['delivery_uncertain']},
            {**evidence,'cooldown_seconds':120},
            {**evidence,'owner_id':self.owner.pk+1},
            {**evidence,'owner_channel':{**evidence['owner_channel'],'caller_thread_id':'another'}},
            {**evidence,'runtime_refresh':{**evidence['runtime_refresh'],'prior_activation_digest':'wrong'}},
            {**evidence,'runtime_refresh':{'status':'unapproved'}},
        ]:
            self.assertIsNone(_notification_proof_channel(self.policy,changed))
        previous.evidence['extra']='tampered';previous.save()
        self.assertIsNone(_notification_proof_channel(self.policy,evidence))

    def test_changed_scope_revokes_approval(self):
        self.operator.scope='changed';self.operator.save()
        self.assertEqual(self.cycle(self.fault()),0)

    def test_completed_turn_without_verified_analysis_does_not_open_another_batch(self):
        from datetime import timedelta
        from django.utils import timezone
        self.cycle(self.fault());job=ModelJob.objects.get()
        job.state='completed';job.save(update_fields=['state'])
        self.policy.last_sent_at=timezone.now()-timedelta(seconds=301);self.policy.save(update_fields=['last_sent_at'])
        changed={'dispatch:2:uncertain':('delivery_uncertain',{'dispatch_id':2})}
        self.assertEqual(self.cycle(changed),0)
        self.assertEqual(ModelJob.objects.count(),1)
        document(self.project,self.operator.key,f'operator-analysis:{job.pk}','reviewed',{'job_id':job.pk,'decision':'needs supervisor'})
        self.assertEqual(self.cycle(changed),1)

    def test_following_a_new_caller_requires_a_verified_completed_transfer(self):
        from .operator_monitor import activation_valid
        from execution_context.models import AuthorityTransferJournal
        receipt=self.policy.activation_receipt
        evidence={**receipt.evidence,'follow_verified_owner_handoffs':True}
        self.policy.activation_receipt=document(self.project,self.operator.key,'follow-activation','follow verified transfers',evidence)
        self.policy.save(update_fields=['activation_receipt'])
        changed={**self.config,'caller_thread_id':str(uuid.uuid4())}
        self.assertFalse(activation_valid(self.policy,changed))
        approval=document(self.project,'backend','transfer-approval','approved',
            {'status':'authority_transfer_execute_authorized','journal_key':'f'*64,'fingerprint':'f'*64,
             'project_id':self.project.pk,'source_id':self.caller.pk,'target_id':self.operator.pk,
             'owner_id':self.owner.pk,'old_config_sha256':'b'*64,'new_config_sha256':'a'*64})
        prep=document(self.project,'backend','transfer-preparation','prepared',
            {'status':'authority_transfer_prepared_only','fingerprint':'f'*64})
        journal=AuthorityTransferJournal.objects.create(key='f'*64,project=self.project,source=self.caller,target=self.operator,
            owner=self.owner,preparation=prep,authorization=approval,state='complete',old_config_sha256='b'*64,
            new_config_sha256='a'*64,after_snapshot={'caller_thread_id':changed['caller_thread_id']})
        self.assertTrue(activation_valid(self.policy,changed))
        journal.state='ambiguous';journal.save(update_fields=['state'])
        self.assertFalse(activation_valid(self.policy,changed))

    def test_detector_ignores_recent_and_resolved_transfer(self):
        from datetime import timedelta
        from django.utils import timezone
        from execution_context.models import AuthorityTransferJournal
        from .operator_monitor import conditions
        row=AuthorityTransferJournal.objects.create(key='f'*64,project=self.project,source=self.caller,target=self.operator,
            owner=self.owner,preparation=self.policy.activation_receipt,state='ambiguous',old_config_sha256='a'*64)
        self.assertEqual(conditions(self.policy,timezone.now()),{})
        AuthorityTransferJournal.objects.filter(pk=row.pk).update(updated_at=timezone.now()-timedelta(seconds=121))
        self.assertIn(f'transfer:{row.pk}',conditions(self.policy,timezone.now()))
        AuthorityTransferJournal.objects.filter(pk=row.pk).update(state='complete')
        self.assertEqual(conditions(self.policy,timezone.now()),{})

    def test_resolved_before_dispatch_cancels_only_the_monitor_request(self):
        self.cycle(self.fault());job=ModelJob.objects.get()
        self.cycle();job.refresh_from_db()
        self.assertEqual(job.state,'cancelled')
        self.assertFalse(hasattr(job,'session_dispatch'))

    def test_phase_change_keeps_the_same_open_incident(self):
        self.cycle({'transfer:1':('authority_transfer_frozen',{'journal_id':1})})
        self.cycle({'transfer:1':('authority_transfer_ambiguous',{'journal_id':1})})
        self.assertEqual(OperatorIncident.objects.count(),1)
        self.assertEqual(OperatorIncident.objects.get().code,'authority_transfer_ambiguous')
        self.assertEqual(ModelJob.objects.count(),1)

    def test_detector_only_current_uncertain_delivery(self):
        from django.utils import timezone
        from execution_context.models import SessionDispatch
        from .operator_monitor import conditions
        job=ModelJob.objects.create(charter=self.caller,owner=self.owner,prompt='test',state='needs_review')
        row=SessionDispatch.objects.create(job=job,target_charter=self.caller,target_thread_id='retired-review6',
            dispatch_key=uuid.uuid4(),state='acknowledged',category='text')
        self.assertEqual(conditions(self.policy,timezone.now()),{})
        row.state='uncertain';row.save()
        self.assertEqual(conditions(self.policy,timezone.now()),{})
        row.target_thread_id=self.caller.thread_id;row.save()
        self.assertEqual(conditions(self.policy,timezone.now())[f'dispatch:{row.pk}:uncertain'][0],'delivery_uncertain')

    def test_real_transfer_transition_preserves_open_incident(self):
        from datetime import timedelta
        from django.utils import timezone
        from execution_context.models import AuthorityTransferJournal
        row=AuthorityTransferJournal.objects.create(key='c'*64,project=self.project,source=self.caller,target=self.operator,
            owner=self.owner,preparation=self.policy.activation_receipt,state='frozen',old_config_sha256='a'*64)
        AuthorityTransferJournal.objects.filter(pk=row.pk).update(updated_at=timezone.now()-timedelta(seconds=121))
        with patch('operations.operator_monitor.load_config',return_value=self.config):
            self.assertEqual(inspect_policy(self.policy.pk),1)
            row.state='ambiguous';row.save()
            self.assertEqual(inspect_policy(self.policy.pk),0)
        incident=OperatorIncident.objects.get()
        self.assertEqual((incident.state,incident.generation,incident.code),('open',1,'authority_transfer_ambiguous'))
        self.assertEqual(ModelJob.objects.count(),1)


class OperatorMonitorConcurrencyTests(TransactionTestCase):
    setUp=OperatorMonitorTests.setUp

    def test_simultaneous_cycles_create_one_batch(self):
        import threading
        from django.db import close_old_connections,OperationalError
        gate=threading.Barrier(2);errors=[]
        def run():
            close_old_connections();gate.wait()
            try:inspect_policy(self.policy.pk)
            except OperationalError:pass  # SQLite lock contention retries on the next maintenance tick.
            except Exception as exc:errors.append(exc)
            finally:close_old_connections()
        with patch('operations.operator_monitor.load_config',return_value=self.config),patch('operations.operator_monitor.conditions',return_value={'dispatch:1:uncertain':('delivery_uncertain',{'dispatch_id':1})}):
            threads=[threading.Thread(target=run) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=10)
            self.assertTrue(all(not t.is_alive() for t in threads));self.assertEqual(errors,[])
            inspect_policy(self.policy.pk)
        self.assertEqual(ModelJob.objects.count(),1)
        self.assertEqual(OperatorIncident.objects.count(),1)
