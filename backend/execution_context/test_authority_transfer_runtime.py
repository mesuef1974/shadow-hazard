import hashlib,json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from workspace.models import Project,ModelPolicy,SessionCharter,ProjectDocumentation,AutoRenewalPolicy
from execution_context.authority_handover import _channel
from .models import AuthorityTransferJournal
from .authority_transfer_runtime import execute_transfer,recover_transfer,rollback_transfer,digest,TransferUncertain


class AuthorityTransferRuntimeTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create(username='mesuef',role='supervisor')
        self.project=Project.objects.create(name='test')
        policy=ModelPolicy.objects.create(category='test')
        self.source=SessionCharter.objects.create(key='backend',project=self.project,policy=policy,thread_id='source')
        self.target=SessionCharter.objects.create(key='coordination',project=self.project,policy=policy,thread_id='target')
        self.old={'caller_thread_id':'source','project_id':self.project.pk,'authorizing_user_id':self.owner.pk,
            'server_sha256':'a'*64,'owner_identity':{'sha256':'b'*64},'pipe':'pipe-old','extra':'kept'}
        self.new={**self.old,'caller_thread_id':'target'}
        prep_evidence={'status':'authority_transfer_prepared_only','fingerprint':'f'*64,'proposed_config_sha256':digest(self.new),
            'charters':[],'renewal_policies_and_approvals':{'items':[]}}
        self.preparation=ProjectDocumentation.objects.create(project=self.project,session_key='backend',task_key='prep',summary='prep',evidence=prep_evidence,digest='prep')
        self.journal=AuthorityTransferJournal.objects.create(key='f'*64,project=self.project,source=self.source,target=self.target,
            owner=self.owner,preparation=self.preparation,before_snapshot={'source_thread_id':'source','target_thread_id':'target','policies':[]},
            old_config_sha256=digest(self.old),state='prepared')
        self.authorization=self.receipt('authority_transfer_execute_authorized',self.old)
        self.journal.new_config_sha256=digest(self.new);self.journal.save(update_fields=['new_config_sha256'])
        self.rollback_authorization=self.receipt('authority_transfer_rollback_authorized',self.old)

    def receipt(self,status,old):
        evidence={'status':status,'journal_key':self.journal.key,'fingerprint':self.journal.key,
            'owner_id':self.owner.pk,'project_id':self.project.pk,'source_id':self.source.pk,'target_id':self.target.pk,
            'old_config_sha256':digest(old),'new_config_sha256':digest(self.new)}
        payload={'summary':'authorized','evidence':evidence}
        row=ProjectDocumentation.objects.create(project=self.project,session_key='backend',task_key=status,
            summary=payload['summary'],evidence=evidence,digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
        return row

    def approved_policy(self,key):
        charter=SessionCharter.objects.filter(key=key).first()
        if charter is None:
            policy=ModelPolicy.objects.create(category=key)
            charter=SessionCharter.objects.create(key=key,project=self.project,policy=policy,thread_id=key+'-thread')
        evidence={'status':'automatic_renewal_activation_authorized','owner_channel':{
            'caller_thread_id':'source','server_sha256':'a'*64,'owner_image_sha256':'b'*64}}
        payload={'summary':'approved','evidence':evidence}
        receipt=ProjectDocumentation.objects.create(project=self.project,session_key=key,task_key='activation-'+key,
            summary=payload['summary'],evidence=evidence,digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
        return AutoRenewalPolicy.objects.create(charter=charter,enabled=True,owner=self.owner,activation_receipt=receipt)

    def execute(self):
        self.refresh_preparation()
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True), \
            patch('execution_context.authority_transfer_runtime.load_config',side_effect=[self.old,self.old,self.old,self.new]), \
            patch('execution_context.authority_transfer_runtime.pipe_identity',return_value=self.old['owner_identity']), \
            patch('execution_context.authority_transfer_runtime._idle_proofs',return_value={'1':{'desktop_idle':True,'rollout_sha256':'c'*64}}), \
            patch('execution_context.authority_transfer_runtime.save_config') as save:
            result=execute_transfer(self.journal.pk,self.owner,self.authorization.pk)
        self.assertEqual(result.state,'complete')
        self.assertFalse(result.execution_enabled)
        self.assertEqual(result.new_config_sha256,digest(self.new))
        save.assert_called_once_with(self.new,expected=self.old)
        self.assertEqual(self.source.thread_id,'source');self.assertEqual(self.target.thread_id,'target')
        return result

    def refresh_preparation(self):
        from .authority_transfer_runtime import _policy_images
        chars,items=_policy_images(self.project.pk)
        evidence={'status':'authority_transfer_prepared_only','fingerprint':self.journal.key,
            'proposed_config_sha256':digest(self.new),'charters':chars,'renewal_policies_and_approvals':{'items':items}}
        payload={'summary':'prep','evidence':evidence}
        self.preparation.evidence=evidence
        self.preparation.digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.preparation.save(update_fields=['evidence','digest'])

    def test_execute_cas_and_activation_derivatives_no_binding_mutation(self):
        front=self.approved_policy('frontend');review=self.approved_policy('review')
        coordination=self.approved_policy('coordination')
        backend=self.approved_policy('backend');backend.enabled=False;backend.save(update_fields=['enabled'])
        result=self.execute()
        for policy in [front,review]:
            policy.refresh_from_db();self.assertEqual(policy.activation_receipt.evidence['owner_channel']['caller_thread_id'],'target')
        coordination.refresh_from_db();self.assertFalse(coordination.enabled)
        backend.refresh_from_db();self.assertFalse(backend.enabled)
        self.assertEqual(self.source.thread_id,'source');self.assertEqual(self.target.thread_id,'target')

    def test_missing_or_nondelegated_approval_fails_before_rpc(self):
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True),patch('execution_context.authority_transfer_runtime.load_config',return_value=self.old),patch('execution_context.authority_transfer_runtime.OwnerTools') as rpc:
            with self.assertRaises(PermissionDenied):execute_transfer(self.journal.pk,self.owner,self.preparation.pk)
            rpc.assert_not_called()

    def test_dual_idle_proof_uses_two_readonly_sources_per_thread(self):
        from unittest.mock import MagicMock
        from .authority_transfer_runtime import _idle_proofs
        client=MagicMock()
        client.__enter__.return_value=client
        client.call.side_effect=lambda method,args,**kwargs:{'thread':{'id':args['threadId'],'kind':'codex','hostId':'local',
            'cwd':str(__import__('django.conf').conf.settings.BASE_DIR.parent),'status':{'type':'idle','activeFlags':[]}},
            'turns':[{'id':'turn1','status':'completed'}]}
        snapshots=[{'meta':{'session_id':tid,'cwd':str(__import__('django.conf').conf.settings.BASE_DIR.parent)},'completed':True,'latest_turn':'turn1','sha256':'c'*64}
            for tid in ['source','source','target','target']]
        with patch('execution_context.authority_transfer_runtime.OwnerTools',return_value=client), \
             patch('execution_context.authority_transfer_runtime.load_config',return_value=self.old), \
             patch('execution_context.authority_transfer_runtime.pipe_identity',return_value=self.old['owner_identity']), \
             patch('execution_context.authority_transfer_runtime.rollout_snapshot',side_effect=snapshots):
            evidence=_idle_proofs(self.journal,self.old)
        self.assertEqual(set(evidence),{str(self.source.pk),str(self.target.pk)})
        self.assertEqual(client.call.call_count,2)

    def test_crash_after_config_write_recovers_without_second_write(self):
        self.refresh_preparation()
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True), \
            patch('execution_context.authority_transfer_runtime.load_config',side_effect=[self.old,self.old,self.old,self.new,self.new]), \
            patch('execution_context.authority_transfer_runtime.pipe_identity',return_value=self.old['owner_identity']), \
            patch('execution_context.authority_transfer_runtime._idle_proofs',return_value={'1':{'desktop_idle':True,'rollout_sha256':'c'*64}}), \
            patch('execution_context.authority_transfer_runtime.save_config'), \
            patch('execution_context.authority_transfer_runtime._commit_bindings',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):execute_transfer(self.journal.pk,self.owner,self.authorization.pk)
        self.journal.refresh_from_db();self.assertEqual(self.journal.state,'ambiguous')
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True),patch('execution_context.authority_transfer_runtime.load_config',return_value=self.new):
            recovered=recover_transfer(self.journal.pk,self.owner)
        self.assertEqual(recovered.state,'complete')

    def test_crash_before_config_write_releases_lease_without_retry(self):
        self.refresh_preparation()
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True), \
            patch('execution_context.authority_transfer_runtime.load_config',side_effect=[self.old,self.old,self.old,self.old]), \
            patch('execution_context.authority_transfer_runtime.pipe_identity',return_value=self.old['owner_identity']), \
            patch('execution_context.authority_transfer_runtime._idle_proofs',return_value={'1':{'desktop_idle':True}}), \
            patch('execution_context.authority_transfer_runtime.save_config',side_effect=RuntimeError('write did not happen')) as save:
            with self.assertRaises(RuntimeError):execute_transfer(self.journal.pk,self.owner,self.authorization.pk)
            recovered=recover_transfer(self.journal.pk,self.owner)
        self.assertEqual(recovered.state,'prepared')
        self.assertFalse(recovered.execution_enabled)
        save.assert_called_once()

    def test_unknown_config_hash_quarantines(self):
        self.refresh_preparation()
        self.journal.state='ambiguous';self.journal.authorization=self.authorization;self.journal.new_config_sha256=digest(self.new);self.journal.lease_key='lease';self.journal.save()
        unknown={**self.new,'extra':'changed'}
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True),patch('execution_context.authority_transfer_runtime.load_config',return_value=unknown):
            with self.assertRaises(TransferUncertain):recover_transfer(self.journal.pk,self.owner)
        self.journal.refresh_from_db();self.assertEqual(self.journal.state,'ambiguous')

    def test_interrupted_rollback_does_not_overwrite_a_newer_policy(self):
        front=self.approved_policy('frontend')
        completed=self.execute()
        front.refresh_from_db()
        front.enabled=False;front.save(update_fields=['enabled'])
        completed.state='ambiguous';completed.rollback_authorization=self.rollback_authorization
        completed.after_snapshot={**completed.after_snapshot,'rollback_pending':{
            'config_sha256':digest(self.old),'lease_key':completed.lease_key}}
        completed.save()
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True), \
                patch('execution_context.authority_transfer_runtime.load_config',return_value=self.old), \
                patch('execution_context.authority_transfer_runtime.save_config') as save:
            with self.assertRaises(TransferUncertain):recover_transfer(completed.pk,self.owner)
        front.refresh_from_db();self.assertFalse(front.enabled)
        save.assert_not_called()

    def test_second_transfer_cannot_acquire_the_same_project(self):
        self.refresh_preparation()
        AuthorityTransferJournal.objects.create(key='e'*64,project=self.project,source=self.source,
            target=self.target,owner=self.owner,preparation=self.preparation,state='frozen',
            old_config_sha256=digest(self.old))
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True), \
                patch('execution_context.authority_transfer_runtime.load_config',return_value=self.old), \
                patch('execution_context.authority_transfer_runtime.save_config') as save:
            with self.assertRaises(ValidationError):execute_transfer(self.journal.pk,self.owner,self.authorization.pk)
        save.assert_not_called()

    def test_rollback_recovery_rechecks_config_inside_the_database_commit(self):
        front=self.approved_policy('frontend')
        completed=self.execute();front.refresh_from_db()
        receipt_after=front.activation_receipt_id
        completed.state='ambiguous';completed.rollback_authorization=self.rollback_authorization
        completed.after_snapshot={**completed.after_snapshot,'rollback_pending':{
            'config_sha256':digest(self.old),'lease_key':completed.lease_key}}
        completed.save()
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True), \
                patch('execution_context.authority_transfer_runtime.load_config',side_effect=[self.old,{**self.old,'extra':'changed'}]):
            with self.assertRaises(TransferUncertain):recover_transfer(completed.pk,self.owner)
        completed.refresh_from_db();front.refresh_from_db()
        self.assertEqual(completed.state,'ambiguous')
        self.assertEqual(front.activation_receipt_id,receipt_after)

    def test_owner_deactivated_after_freeze_cannot_commit(self):
        self.refresh_preparation()
        def revoked(*args):
            get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
            return {'1':{'desktop_idle':True,'rollout_sha256':'c'*64}}
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True), \
                patch('execution_context.authority_transfer_runtime.load_config',side_effect=[self.old,self.old,self.old,self.new]), \
                patch('execution_context.authority_transfer_runtime._idle_proofs',side_effect=revoked), \
                patch('execution_context.authority_transfer_runtime.save_config'):
            with self.assertRaises(PermissionDenied):
                execute_transfer(self.journal.pk,self.owner,self.authorization.pk)
        self.journal.refresh_from_db()
        self.assertNotEqual(self.journal.state,'complete')

    def test_rollback_requires_new_owner_receipt_and_cas_and_restores_prior_policies(self):
        completed=self.execute();front=self.approved_policy('frontend')
        # Post-transfer work invalidates rollback even when the connector still matches.
        from workspace.models import ModelJob
        ModelJob.objects.create(charter=self.target,owner=self.owner,prompt='new work',requested_model='gpt-6-luna',requested_thinking='low',state='queued')
        with override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True),patch('execution_context.authority_transfer_runtime.load_config',return_value=self.new),patch('execution_context.authority_transfer_runtime._idle_proofs',return_value={'1':{'desktop_idle':True}}):
            with self.assertRaises(TransferUncertain):rollback_transfer(completed.pk,self.owner,self.rollback_authorization.pk)
