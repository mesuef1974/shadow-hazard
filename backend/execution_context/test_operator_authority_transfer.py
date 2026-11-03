from contextlib import nullcontext
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.core.exceptions import PermissionDenied,ValidationError
from workspace.models import SessionCharter,AutoRenewalPolicy
from workspace.memory import document
from operations.models import OperatorMonitorPolicy
from .models import AuthorityTransferJournal
from .authority_handover import prepare_transfer,monitor_images
from .authority_transfer_runtime import execute_transfer,recover_transfer,rollback_transfer,TransferUncertain
from . import test_authority_transfer_runtime as runtime_tests


@override_settings(AUTHORITY_TRANSFER_EXECUTION_ENABLED=True)
class OperatorAuthorityTransferTests(TestCase):
    approved_policy=runtime_tests.AuthorityTransferRuntimeTests.approved_policy
    receipt=runtime_tests.AuthorityTransferRuntimeTests.receipt

    def setUp(self):
        runtime_tests.AuthorityTransferRuntimeTests.setUp(self)
        self.target.key='session_operator';self.target.save()
        self.source.key='coordination';self.source.save()
        self.renewals=[self.approved_policy(k) for k in ['backend','frontend','review']]
        self.coordination=AutoRenewalPolicy.objects.create(charter=self.source,owner=self.owner,enabled=False)
        activation=document(self.project,'session_operator','monitor-initial','approved',{
            'status':'operator_monitor_activation_authorized','owner_channel':{'caller_thread_id':'source',
            'server_sha256':'a'*64,'owner_image_sha256':'b'*64},'scope':'unchanged'})
        self.monitor=OperatorMonitorPolicy.objects.create(project=self.project,charter=self.target,
            owner=self.owner,enabled=True,activation_receipt=activation,scopes=['delivery_uncertain'])
        # Remove the fixture's unused prepared journal, without touching production data.
        self.journal.state='blocked';self.journal.save()
        with patch('execution_context.authority_handover.load_config',return_value=self.old):
            prep=prepare_transfer(self.owner,self.target.pk)
        self.journal=AuthorityTransferJournal.objects.get(key=prep.evidence['fingerprint'])
        e={'status':'authority_transfer_execute_authorized','journal_key':self.journal.key,'fingerprint':self.journal.key,
            'owner_id':self.owner.pk,'project_id':self.project.pk,'source_id':self.source.pk,'target_id':self.target.pk,
            'old_config_sha256':self.journal.old_config_sha256,'new_config_sha256':self.journal.new_config_sha256,
            'migrate_operator_monitor':True}
        self.authorization=document(self.project,'coordination','transfer-approved','approved',e)
        self.rollback_authorization=document(self.project,'coordination','rollback-approved','rollback',
            {**e,'status':'authority_transfer_rollback_authorized'})
        self.proofs={str(c.pk):{'latest_turn':'idle-turn','desktop_idle':True} for c in [self.source,self.target]}
        self.config=[self.old]
        self.before_monitor=monitor_images(self.project.pk)

    def io(self):
        from contextlib import ExitStack
        stack=ExitStack()
        stack.enter_context(patch('execution_context.authority_transfer_runtime.load_config',side_effect=lambda:self.config[0]))
        stack.enter_context(patch('execution_context.authority_transfer_runtime._idle_proofs',return_value=self.proofs))
        stack.enter_context(patch('execution_context.authority_transfer_runtime._configuration_lock',side_effect=nullcontext))
        def save(new,expected):
            self.assertEqual(self.config[0],expected);self.config[0]=new
        self.save=stack.enter_context(patch('execution_context.authority_transfer_runtime.save_config',side_effect=save))
        return stack

    def execute(self):return execute_transfer(self.journal.pk,self.owner,self.authorization.pk)

    def test_coordination_to_operator_preserves_renewals_and_migrates_monitor(self):
        with self.io():row=self.execute()
        self.assertEqual(row.state,'complete');self.assertEqual(self.config[0],self.new)
        for p in self.renewals:
            p.refresh_from_db();self.assertTrue(p.enabled)
            self.assertEqual(p.activation_receipt.evidence['owner_channel']['caller_thread_id'],'target')
        self.coordination.refresh_from_db();self.assertFalse(self.coordination.enabled)
        self.monitor.refresh_from_db();self.assertTrue(self.monitor.enabled)
        self.assertEqual(self.monitor.activation_receipt.evidence['authority_transfer_journal'],row.key)
        self.assertEqual(self.monitor.activation_receipt.evidence['prior_activation_receipt_id'],self.before_monitor[0]['receipt_id'])
        self.assertEqual(row.after_snapshot['operator_monitor'][0]['after'],monitor_images(self.project.pk)[0])
        self.assertEqual(self.source.thread_id,'source');self.assertEqual(self.target.thread_id,'target')

    def test_monitor_cas_before_config_write(self):
        self.monitor.cooldown_seconds=400;self.monitor.save()
        with self.io():
            with self.assertRaises(TransferUncertain):self.execute()
            self.save.assert_not_called()

    def test_operator_charter_cas_before_config_write(self):
        self.target.rules='changed';self.target.save()
        with self.io():
            with self.assertRaises(TransferUncertain):self.execute()
            self.save.assert_not_called()

    def test_monitor_requires_explicit_migration_permission(self):
        e={**self.authorization.evidence,'migrate_operator_monitor':False}
        denied=document(self.project,'coordination','no-monitor-permission','denied',e)
        with self.io():
            with self.assertRaises(PermissionDenied):execute_transfer(self.journal.pk,self.owner,denied.pk)
            self.save.assert_not_called()

    def test_recovery_completes_once_without_second_write(self):
        with self.io():
            with patch('execution_context.authority_transfer_runtime._commit_bindings',side_effect=RuntimeError('after file')):
                with self.assertRaises(RuntimeError):self.execute()
            row=recover_transfer(self.journal.pk,self.owner)
            self.save.assert_called_once()
        self.assertEqual(row.state,'complete')
        self.monitor.refresh_from_db();self.assertEqual(self.monitor.activation_receipt.evidence['owner_channel']['caller_thread_id'],'target')

    def test_rollback_restores_monitor_and_renewals(self):
        before=[(p.pk,p.activation_receipt_id) for p in self.renewals]
        with self.io():
            self.execute();row=rollback_transfer(self.journal.pk,self.owner,self.rollback_authorization.pk)
        self.assertEqual(row.state,'rolled_back');self.assertEqual(self.config[0],self.old)
        self.assertEqual(monitor_images(self.project.pk),self.before_monitor)
        self.assertEqual(list(AutoRenewalPolicy.objects.filter(pk__in=[p[0] for p in before]).order_by('pk').values_list('pk','activation_receipt_id')),before)

    def test_changed_monitor_after_image_blocks_rollback(self):
        with self.io():
            self.execute();self.monitor.cooldown_seconds=401;self.monitor.save(update_fields=['cooldown_seconds'])
            with self.assertRaises(TransferUncertain):rollback_transfer(self.journal.pk,self.owner,self.rollback_authorization.pk)
            self.assertEqual(self.save.call_count,1)

    def test_new_desktop_turn_between_rollback_idle_reads_blocks_file_write(self):
        with self.io():
            self.execute()
            changed={key:{**value,'latest_turn':'new-completed-turn'} for key,value in self.proofs.items()}
            with patch('execution_context.authority_transfer_runtime._idle_proofs',side_effect=[self.proofs,changed]):
                with self.assertRaises(TransferUncertain):
                    rollback_transfer(self.journal.pk,self.owner,self.rollback_authorization.pk)
            self.assertEqual(self.save.call_count,1)
            self.assertEqual(self.config[0],self.new)

    def test_interrupted_rollback_recovers_monitor_without_file_rewrite(self):
        with self.io():
            row=self.execute()
            row.state='frozen';row.rollback_authorization=self.rollback_authorization
            row.after_snapshot={**row.after_snapshot,'rollback_pending':{'config_sha256':row.old_config_sha256,'lease_key':row.lease_key}}
            row.save();self.config[0]=self.old
            recovered=recover_transfer(row.pk,self.owner)
            self.assertEqual(self.save.call_count,1)
        self.assertEqual(recovered.state,'rolled_back');self.assertEqual(monitor_images(self.project.pk),self.before_monitor)

    def test_target_renewal_only_is_suspended(self):
        target_policy=self.approved_policy('session_operator')
        with patch('execution_context.authority_handover.load_config',return_value=self.old):
            prep=prepare_transfer(self.owner,self.target.pk)
        self.journal=AuthorityTransferJournal.objects.get(key=prep.evidence['fingerprint'])
        self.authorization=document(self.project,'coordination','target-renewal-approved','approved',{
            **self.authorization.evidence,'journal_key':self.journal.key,'fingerprint':self.journal.key})
        with self.io():self.execute()
        target_policy.refresh_from_db();self.assertFalse(target_policy.enabled)
        self.assertTrue(all(AutoRenewalPolicy.objects.get(pk=p.pk).enabled for p in self.renewals))

    def test_inflight_job_and_caller_change_block_before_file_write(self):
        from workspace.models import ModelJob
        ModelJob.objects.create(charter=self.source,owner=self.owner,prompt='inflight',state='queued')
        with self.io():
            with self.assertRaises(TransferUncertain):self.execute()
            self.save.assert_not_called()

    def test_unregistered_caller_and_other_project_target_rejected(self):
        from workspace.models import Project
        with patch('execution_context.authority_handover.load_config',return_value={**self.old,'caller_thread_id':'unregistered'}):
            with self.assertRaises(ValidationError):prepare_transfer(self.owner,self.target.pk)
        self.target.project=Project.objects.create(name='other');self.target.save()
        with patch('execution_context.authority_handover.load_config',return_value=self.old):
            with self.assertRaises(ValidationError):prepare_transfer(self.owner,self.target.pk)
