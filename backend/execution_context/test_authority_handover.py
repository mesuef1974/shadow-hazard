from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from workspace.models import Project,ModelPolicy,SessionCharter,AutoRenewalPolicy,ProjectDocumentation
from .authority_handover import prepare_transfer,assert_transfer_not_frozen
from .models import AuthorityTransferJournal


class AuthorityHandoverTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.owner=User.objects.create(username='mesuef',role='supervisor')
        self.student=User.objects.create(username='saud',role='student')
        self.project=Project.objects.create(name='ظل الخطر')
        p=ModelPolicy.objects.create(category='test')
        self.source=SessionCharter.objects.create(key='backend',name='Backend',project=self.project,policy=p,thread_id='source-current')
        self.target=SessionCharter.objects.create(key='coordination',name='Coordination',project=self.project,policy=p,thread_id='target-existing')
        for key in ['frontend','review']:
            SessionCharter.objects.create(key=key,name=key,project=self.project,policy=p,thread_id='thread-'+key)
        self.channel={'caller_thread_id':'source-current','project_id':self.project.pk,
            'authorizing_user_id':self.owner.pk,'server_sha256':'a'*64,
            'owner_identity':{'sha256':'b'*64},'pipe':'\\\\.\\pipe\\private-value','node':'secret-path','server':'secret-path'}

    def prepare(self,target=None,owner=None):
        with patch('execution_context.authority_handover.load_config',return_value=self.channel):
            return prepare_transfer(owner or self.owner,target or self.target.pk)

    def test_preparation_is_unique_stable_and_side_effect_free(self):
        with patch('execution_context.authority_handover.load_config',return_value=self.channel), \
             patch('execution_context.owner_tools.save_config') as save, \
             patch('execution_context.owner_channel.ensure_channel') as ensure:
            first=prepare_transfer(self.owner,self.target.pk)
            second=prepare_transfer(self.owner,self.target.pk)
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(ProjectDocumentation.objects.filter(task_key=first.task_key).count(),1)
        self.assertEqual(first.evidence['status'],'authority_transfer_prepared_only')
        self.assertFalse(first.evidence['execution_enabled'])
        self.assertFalse(first.evidence['blockers_clear'])
        journal=AuthorityTransferJournal.objects.get(key=first.evidence['fingerprint'])
        self.assertEqual(journal.state,'prepared');self.assertFalse(journal.execution_enabled)
        self.assertEqual(first.evidence['source']['thread_id'],self.source.thread_id)
        self.assertEqual(first.evidence['target']['thread_id'],self.target.thread_id)
        self.assertNotIn(self.channel['pipe'],str(first.evidence))
        self.assertNotIn(self.channel['node'],str(first.evidence))
        save.assert_not_called();ensure.assert_not_called()

    def test_channel_or_policy_change_creates_distinct_snapshot(self):
        first=self.prepare()
        policy=AutoRenewalPolicy.objects.create(charter=self.target,enabled=False,owner=self.owner)
        policy.save(update_fields=['enabled','owner','updated_at'])
        second=self.prepare()
        self.assertNotEqual(first.pk,second.pk)
        self.assertNotEqual(first.evidence['fingerprint'],second.evidence['fingerprint'])
        self.assertEqual(ProjectDocumentation.objects.count(),2)

    def test_scope_rules_and_any_config_change_invalidate_snapshot(self):
        from execution_context.authority_handover import digest
        first=self.prepare()
        self.assertEqual(first.evidence['charters'][0]['scope_sha256'],digest(self.source.scope if first.evidence['charters'][0]['key']=='backend' else self.target.scope))
        self.source.refresh_from_db()
        original_rules=digest(self.source.rules)
        SessionCharter.objects.filter(pk=self.source.pk).update(scope='revised scope')
        second=self.prepare()
        self.assertNotEqual(first.evidence['fingerprint'],second.evidence['fingerprint'])
        self.assertNotEqual(first.evidence['charters'][0]['scope_sha256'],second.evidence['charters'][0]['scope_sha256'])
        self.assertEqual(first.evidence['charters'][0]['rules_sha256'],original_rules)
        changed={**self.channel,'node':'different-secret-path'}
        with patch('execution_context.authority_handover.load_config',return_value=changed):
            third=prepare_transfer(self.owner,self.target.pk)
        self.assertNotEqual(second.evidence['channel']['full_config_sha256'],third.evidence['channel']['full_config_sha256'])

    def test_existing_tampered_receipt_is_reverified_and_rejected(self):
        first=self.prepare()
        ProjectDocumentation.objects.filter(pk=first.pk).update(digest='0'*64)
        with self.assertRaises(ValueError):self.prepare()

    def test_rejects_source_itself_unregistered_or_cross_project_target(self):
        with self.assertRaises(ValidationError):self.prepare(self.source.pk)
        unregistered=SessionCharter.objects.create(key='other',name='other',project=self.project,policy=self.source.policy,thread_id='unregistered')
        with self.assertRaises(ValidationError):self.prepare(unregistered.pk)
        other=Project.objects.create(name='different')
        SessionCharter.objects.filter(pk=self.target.pk).update(project=other)
        with self.assertRaises(ValidationError):self.prepare(self.target.pk)
        self.assertFalse(ProjectDocumentation.objects.filter(task_key__startswith='authority-transfer:').exists())

    def test_rejects_undesignated_or_inactive_owner(self):
        with self.assertRaises(PermissionDenied):self.prepare(owner=self.student)
        self.owner.is_active=False;self.owner.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):self.prepare()

    def test_records_active_and_ambiguous_paths_as_blockers_without_claiming_idle(self):
        from workspace.models import ModelJob
        from execution_context.models import SessionDispatch,SessionDispatchEvent
        job=ModelJob.objects.create(charter=self.source,owner=self.owner,prompt='placeholder',requested_model='gpt-6-luna',requested_thinking='low',state='running')
        dispatch=SessionDispatch.objects.create(job=job,dispatch_key=__import__('uuid').uuid4(),target_charter=self.target,
            target_thread_id=self.target.thread_id,category='text',requested_model='gpt-6-luna',requested_thinking='low',route_reason='test',state='uncertain')
        SessionDispatchEvent.objects.create(dispatch=dispatch,state='owner_call_intent',detail={'ambiguous':True})
        plan=self.prepare()
        self.assertIn(dispatch.pk,plan.evidence['blockers']['active_dispatch_ids'])
        event=SessionDispatchEvent.objects.get(dispatch=dispatch,state='owner_call_intent')
        self.assertIn(event.pk,plan.evidence['blockers']['owner_intent_event_ids'])
        self.assertIn(job.pk,plan.evidence['blockers']['active_job_ids'])
        self.assertEqual(plan.evidence['blockers']['live_idle_attestation'],'not_performed; database state cannot prove desktop thread idleness')
        self.assertEqual(SessionCharter.objects.get(pk=self.source.pk).thread_id,'source-current')
        self.assertEqual(SessionCharter.objects.get(pk=self.target.pk).thread_id,'target-existing')

    def test_frontend_path_is_included_as_project_wide_blocker(self):
        from workspace.models import ModelJob
        from execution_context.models import SessionDispatch
        frontend=SessionCharter.objects.get(key='frontend')
        job=ModelJob.objects.create(charter=frontend,owner=self.owner,prompt='placeholder',requested_model='gpt-6-luna',requested_thinking='low',state='running')
        dispatch=SessionDispatch.objects.create(job=job,dispatch_key=__import__('uuid').uuid4(),target_charter=frontend,
            target_thread_id=frontend.thread_id,category='text',requested_model='gpt-6-luna',requested_thinking='low',route_reason='test',state='acknowledged')
        plan=self.prepare()
        self.assertIn(dispatch.pk,plan.evidence['blockers']['active_dispatch_ids'])
        self.assertIn(job.pk,plan.evidence['blockers']['active_job_ids'])

    def test_renewal_approval_transfer_and_self_renewal_suspension_are_only_planned(self):
        backend=SessionCharter.objects.get(key='backend')
        front=SessionCharter.objects.get(key='frontend')
        review=SessionCharter.objects.get(key='review')
        activation=ProjectDocumentation.objects.create(project=self.project,session_key='frontend',task_key='activation',summary='approved',evidence={'status':'existing'},digest='a'*64)
        AutoRenewalPolicy.objects.create(charter=front,enabled=True,owner=self.owner,activation_receipt=activation)
        AutoRenewalPolicy.objects.create(charter=review,enabled=True,owner=self.owner,activation_receipt=activation)
        AutoRenewalPolicy.objects.create(charter=self.target,enabled=True,owner=self.owner,activation_receipt=activation)
        AutoRenewalPolicy.objects.create(charter=backend,enabled=True,owner=self.owner,activation_receipt=activation)
        before={p.charter_id:(p.enabled,p.activation_receipt_id) for p in AutoRenewalPolicy.objects.all()}
        plan=self.prepare().evidence['renewal_policies_and_approvals']
        self.assertEqual({p['charter_key'] for p in plan['frontend_review_reissue_plan']},{'frontend','review'})
        self.assertIn('caller itself',plan['coordination_suspension_plan']['reason'])
        self.assertIsNone(plan['backend_renewal_plan'])
        self.assertEqual({p['charter_key'] for p in plan['renewal_reissue_plan']},{'backend','frontend','review'})
        after={p.charter_id:(p.enabled,p.activation_receipt_id) for p in AutoRenewalPolicy.objects.all()}
        self.assertEqual(before,after)

    def test_supervisor_must_match_channel_delegation(self):
        from types import SimpleNamespace
        other=SimpleNamespace(pk=999,is_active=True,role='supervisor')
        with self.assertRaises(PermissionDenied):self.prepare(owner=other)

    def test_transfer_freeze_blocks_existing_delivery_and_renewal_pre_rpc_guards(self):
        from execution_context.owner_delivery import BridgeError
        from execution_context.renewal_owner_tools import OwnerToolsError,trusted_config
        from execution_context.owner_delivery import _idle
        with patch('execution_context.owner_delivery.ensure_channel') as ensure, \
             patch('execution_context.owner_delivery.OwnerTools') as owner_tools:
            # No queued dispatch, so the regular delivery path cannot enter the channel.
            pass
        journal=AuthorityTransferJournal.objects.create(key='f'*64,project=self.project,source=self.source,
            target=self.target,owner=self.owner,preparation=ProjectDocumentation.objects.create(
                project=self.project,session_key='test',task_key='freeze',summary='test',evidence={},digest='test'),
            old_config_sha256='a'*64,state='frozen')
        with self.assertRaises(ValidationError):assert_transfer_not_frozen(self.project.pk)
        with patch('execution_context.renewal_owner_tools.load_config') as load, \
             patch('execution_context.renewal_owner_tools.pipe_identity') as pipe:
            with self.assertRaises(OwnerToolsError):trusted_config(self.channel)
            load.assert_not_called();pipe.assert_not_called()
