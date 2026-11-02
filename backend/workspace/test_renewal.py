import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from workspace.models import (Project, ModelPolicy, SessionCharter, SessionRenewal,
    SessionRenewalEvent, ProjectDocumentation, ProjectMemory, ModelJob)
from workspace.memory import document
from workspace.renewal import (prepare_request, create_candidate, verify_candidate,
    commit_binding, rollback, RenewalError)


class FakeDesktop:
    supported = True

    def __init__(self, case):
        self.case = case
        self.target = str(uuid.uuid4())
        self.calls = 0
        self.receipt_id = None
        self.states = {}
        self.activity = {}
        self.create_error = None

    def create(self, **kwargs):
        self.calls += 1
        if self.create_error:
            raise self.create_error
        return {'thread_id': self.target}

    def inspect(self, thread_id):
        return {'thread_id': thread_id, 'project_id': self.case.project.pk,
            'cwd': str(settings.BASE_DIR.parent.resolve()),
            'observed_at': timezone.now().isoformat(), 'archived': False,
            'state': self.states.get(thread_id, 'idle'), 'last_turn_complete': True,
            'owner_read_verified': True, 'activity_token': self.activity.get(thread_id, 'bootstrap-only')}

    def delivery_receipt_id(self, request):
        return self.receipt_id


class RenewalFixture:
    def setUp(self):
        self.owner = get_user_model().objects.create(username='mesuef', role='supervisor')
        self.project = Project.objects.create(name='synthetic renewal only')
        self.policy = ModelPolicy.objects.create(category='coordination')
        self.charter = SessionCharter.objects.create(project=self.project, policy=self.policy,
            key='coordination', name='coordination', scope='bounded scope', rules='mandatory rules',
            thread_id=str(uuid.uuid4()))
        self.old = self.charter.thread_id
        self.handover = document(self.project, self.charter.key, 'handover', 'Synthetic agreed handover', {'test_only': True})
        self.key = uuid.uuid4()
        self.row = prepare_request(self.charter.pk, self.owner, key=self.key, handover_id=self.handover.pk)
        self.approval = document(self.project, self.charter.key, 'approval', 'Synthetic execution authority', {
            'renewal_key': str(self.key), 'charter_id': self.charter.pk, 'source_thread_id': self.old,
            'owner_id': self.owner.pk, 'allow_create': True, 'allow_rebind': True, 'allow_rollback': True,
            'automatic': False, 'transport': 'owner-desktop-default'})
        self.adapter = FakeDesktop(self)

    def create(self):
        self.row = create_candidate(self.row.pk, self.owner, approval_id=self.approval.pk, _test_adapter=self.adapter)
        return self.row

    def attest(self, **changes):
        self.row.refresh_from_db()
        turn_id=str(uuid.uuid4())
        self.adapter.activity[self.adapter.target]=turn_id
        evidence = {'status': 'desktop_bootstrap_delivery_verified', 'renewal_key': str(self.key),
            'thread_id': self.adapter.target, 'source_thread_id': self.old,
            'handover_id': self.handover.pk, 'handover_digest': self.row.handover_digest,
            'bootstrap_sha256': self.row.bootstrap_digest, 'context': self.row.context,
            'config_profile': 'default', 'transport': 'owner-desktop-observation',
            'source_evidence': {'source_row': 1, 'thread_id': self.adapter.target,
                'sha256': 'a' * 64, 'turn_id': turn_id, 'observed_at':timezone.now().isoformat()}, **changes}
        receipt = document(self.project, self.charter.key, 'attestation:' + str(uuid.uuid4()),
            'Independently observed synthetic Desktop receipt', evidence)
        self.adapter.receipt_id = receipt.pk
        return receipt

    def verify(self):
        self.attest()
        self.row = verify_candidate(self.row.pk, self.owner, _test_adapter=self.adapter)
        return self.row

    def commit(self):
        self.row = commit_binding(self.row.pk, self.owner, _test_adapter=self.adapter)
        return self.row

    def job(self, state='queued'):
        return ModelJob.objects.create(charter=self.charter, owner=self.owner, prompt='natural task',
            lane=self.charter.pk, requested_model='gpt-6-luna', requested_thinking='low', state=state)


@override_settings(SESSION_RENEWAL_TEST_ADAPTERS=True)
class RenewalTests(RenewalFixture, TestCase):
    def test_preparation_is_idempotent_and_rejects_second_source_request(self):
        same = prepare_request(self.charter.pk, self.owner, key=self.key, handover_id=self.handover.pk)
        self.assertEqual(same.pk, self.row.pk)
        with self.assertRaises(RenewalError):
            prepare_request(self.charter.pk, self.owner, key=uuid.uuid4(), handover_id=self.handover.pk)
        self.assertEqual(SessionRenewal.objects.count(), 1)

    def test_default_adapter_blocks_without_creation_or_lane_reservation(self):
        self.assertEqual(create_candidate(self.row.pk, self.owner).state, 'desktop_blocked')
        from workspace.job_lanes import available_jobs
        job = self.job()
        self.assertTrue(available_jobs(ModelJob.objects.filter(pk=job.pk)).exists())
        self.assertFalse(SessionRenewal.objects.get(pk=self.row.pk).create_attempted)
        self.assertEqual(rollback(self.row.pk, self.owner).state, 'cancelled')

    def test_no_fake_adapter_in_production_even_with_approval(self):
        with override_settings(SESSION_RENEWAL_TEST_ADAPTERS=False), self.assertRaises(RenewalError):
            self.create()
        self.assertEqual(self.adapter.calls, 0)

    def test_creation_once_after_reloads_and_receipt_required_before_binding(self):
        self.create()
        self.create()
        self.assertEqual(self.adapter.calls, 1)
        with self.assertRaises(RenewalError):self.commit()
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id, self.old)
        self.verify();self.commit();self.commit()
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id, self.adapter.target)
        self.assertEqual(SessionRenewalEvent.objects.filter(state='switched').count(), 1)
        self.assertTrue(self.row.binding_receipt_id)

    def test_creation_timeout_and_restart_never_retry_and_reconcile_by_receipt_only(self):
        self.adapter.create_error = TimeoutError()
        self.assertEqual(self.create().state, 'creation_uncertain')
        self.adapter.create_error = None
        self.create();self.assertEqual(self.adapter.calls, 1)
        self.attest()
        self.row = verify_candidate(self.row.pk, self.owner, candidate_thread_id=self.adapter.target, _test_adapter=self.adapter)
        self.assertEqual(self.row.state, 'verified')
        self.commit()

    def test_crash_after_durable_intent_remains_quarantined_without_recreation(self):
        SessionRenewal.objects.filter(pk=self.row.pk).update(state='creating', create_attempted=True, approval=self.approval)
        self.assertEqual(self.create().state, 'creating')
        with self.assertRaises(RenewalError):self.verify()
        self.assertEqual(rollback(self.row.pk, self.owner).state, 'rollback_uncertain')
        self.assertEqual(self.adapter.calls, 0)

    def test_new_or_uncertain_work_blocks_create_and_other_lanes_continue(self):
        for state in ['queued', 'running', 'uncertain', 'waiting_delivery', 'waiting_readonly', 'awaiting_approval', 'needs_review']:
            job = self.job(state)
            with self.assertRaises(RenewalError):self.create()
            job.delete()
        self.assertEqual(self.adapter.calls, 0)

    def retired_review(self):
        from execution_context.models import SessionDispatch
        job = self.job('needs_review')
        job.execution_thread_id = str(uuid.uuid4())
        job.save()
        dispatch = SessionDispatch.objects.create(job=job, dispatch_key=uuid.uuid4(),
            target_charter=self.charter, target_thread_id=job.execution_thread_id,
            category='text', requested_model='gpt-6-luna', requested_thinking='low',
            state='acknowledged')
        return job, dispatch

    def test_exact_retired_review_stays_quarantined_through_renewal(self):
        job, dispatch = self.retired_review()
        self.create(); self.verify(); self.commit()
        job.refresh_from_db(); dispatch.refresh_from_db()
        self.assertEqual(job.state, 'needs_review')
        self.assertEqual(dispatch.state, 'acknowledged')
        self.assertEqual(job.execution_thread_id, dispatch.target_thread_id)
        self.assertNotEqual(dispatch.target_thread_id, self.adapter.target)
        self.assertEqual(self.adapter.calls, 1)

    def test_inexact_or_current_retired_review_still_blocks(self):
        from execution_context.models import SessionDispatchTurn
        for variation in ['current', 'unknown', 'mismatch', 'state', 'job_state',
                          'turn_key', 'turn_record', 'bound_elsewhere', 'lane']:
            with self.subTest(variation=variation):
                job, dispatch = self.retired_review()
                other = None
                if variation == 'current':
                    job.execution_thread_id = dispatch.target_thread_id = self.old
                elif variation == 'unknown':
                    job.execution_thread_id = dispatch.target_thread_id = ''
                elif variation == 'mismatch':
                    job.execution_thread_id = str(uuid.uuid4())
                elif variation == 'state': dispatch.state = 'uncertain'
                elif variation == 'job_state': job.state = 'running'
                elif variation == 'turn_key': dispatch.turn_key = str(uuid.uuid4())
                elif variation == 'turn_record':
                    SessionDispatchTurn.objects.create(dispatch=dispatch,
                        target_thread_id=dispatch.target_thread_id, turn_key=str(uuid.uuid4()))
                elif variation == 'lane': job.lane += 100
                elif variation == 'bound_elsewhere':
                    other = SessionCharter.objects.create(project=self.project, policy=self.policy,
                        key='other', name='other', thread_id=dispatch.target_thread_id)
                job.save(); dispatch.save()
                with self.assertRaises(RenewalError): self.create()
                dispatch.turns.all().delete(); dispatch.delete(); job.delete()
                if other: other.delete()
        self.assertEqual(self.adapter.calls, 0)

    def test_reserved_lane_blocks_dispatch_and_is_released_on_cancel(self):
        self.create()
        job = self.job()
        from workspace.job_lanes import available_jobs, without_renewing_dispatches
        from execution_context.models import SessionDispatch
        dispatch = SessionDispatch.objects.create(job=job, dispatch_key=uuid.uuid4(), target_charter=self.charter,
            target_thread_id=self.old, category='text', requested_model='gpt-6-luna', requested_thinking='low')
        self.assertFalse(available_jobs(ModelJob.objects.filter(pk=job.pk)).exists())
        self.assertFalse(without_renewing_dispatches(SessionDispatch.objects.filter(pk=dispatch.pk)).exists())
        other = ModelJob.objects.create(charter=self.charter, owner=self.owner, prompt='independent lane',
            lane=self.charter.pk + 100, requested_model='gpt-6-luna', requested_thinking='low', state='completed')
        # Same charter stays gated even if a malformed lane number is supplied.
        self.assertFalse(available_jobs(ModelJob.objects.filter(pk=other.pk)).exists())
        dispatch.state='completed';dispatch.save();job.state='completed';job.save()
        rollback(self.row.pk, self.owner, _test_adapter=self.adapter)
        self.assertTrue(available_jobs(ModelJob.objects.filter(pk=job.pk)).exists())

    def test_context_or_handover_drift_blocks_before_external_create(self):
        ProjectMemory.objects.create(project=self.project, key='new-rule', instruction='new required rule')
        with self.assertRaises(RenewalError):self.create()
        self.assertEqual(self.adapter.calls, 0)

    def test_tampered_handover_never_creates(self):
        ProjectDocumentation.objects.filter(pk=self.handover.pk).update(summary='tampered')
        with self.assertRaises(RenewalError):self.create()
        self.assertEqual(self.adapter.calls, 0)

    def test_execution_approval_must_match_request_project_and_explicit_booleans(self):
        for field, value in [('renewal_key', str(uuid.uuid4())), ('allow_create', 1), ('automatic', True)]:
            data={**self.approval.evidence, field:value}
            altered=document(self.project,self.charter.key,'bad:'+str(uuid.uuid4()),'bad scope',data)
            with self.assertRaises(RenewalError):
                create_candidate(self.row.pk,self.owner,approval_id=altered.pk,_test_adapter=self.adapter)
        self.assertEqual(self.adapter.calls,0)

    def test_forged_or_wrong_delivery_evidence_cannot_bind(self):
        self.create()
        for changes in [{'thread_id':str(uuid.uuid4())}, {'handover_digest':'b'*64},
                {'bootstrap_sha256':'b'*64}, {'context':{}}, {'source_evidence':{}}, {'config_profile':'other'}]:
            self.attest(**changes)
            with self.assertRaises(RenewalError):
                verify_candidate(self.row.pk,self.owner,_test_adapter=self.adapter)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_recheck_activity_and_queue_before_binding(self):
        self.create();self.verify()
        self.adapter.activity[self.old]='new source turn'
        with self.assertRaises(RenewalError):self.commit()
        self.adapter.activity.pop(self.old)
        job=self.job()
        with self.assertRaises(RenewalError):self.commit()
        job.delete()
        self.adapter.states[self.adapter.target]='active'
        with self.assertRaises(RenewalError):self.commit()

    def test_compare_and_swap_preserves_binding_changed_by_another_owner(self):
        self.create();self.verify()
        changed=str(uuid.uuid4());SessionCharter.objects.filter(pk=self.charter.pk).update(thread_id=changed)
        with self.assertRaises(RenewalError):self.commit()
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,changed)

    def test_binding_receipt_failure_rolls_back_binding_and_keeps_verified_request(self):
        self.create();self.verify()
        with patch('workspace.renewal._event',side_effect=ValueError('storage failure')):
            with self.assertRaises(ValueError):self.commit()
        self.charter.refresh_from_db();self.row.refresh_from_db()
        self.assertEqual((self.charter.thread_id,self.row.state),(self.old,'verified'))
        self.commit()

    def test_rollback_before_and_after_binding_preserves_history_and_never_recreates(self):
        self.create();self.verify();self.commit()
        total_events=SessionRenewalEvent.objects.count()
        restored=rollback(self.row.pk,self.owner,_test_adapter=self.adapter)
        self.assertEqual(restored.state,'rolled_back')
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)
        self.assertEqual(restored.target_thread_id,self.adapter.target)
        self.assertGreater(SessionRenewalEvent.objects.count(),total_events)
        rollback(self.row.pk,self.owner,_test_adapter=self.adapter)
        self.create();self.assertEqual(self.adapter.calls,1)

    def test_rollback_refuses_completed_new_job_or_new_turn_on_either_thread(self):
        self.create();self.verify();self.commit()
        original_activity=self.adapter.activity.copy()
        job=self.job('completed')
        with self.assertRaises(RenewalError):rollback(self.row.pk,self.owner,_test_adapter=self.adapter)
        job.delete()
        for thread in [self.old,self.adapter.target]:
            self.adapter.activity[thread]='new activity'
            with self.assertRaises(RenewalError):rollback(self.row.pk,self.owner,_test_adapter=self.adapter)
            self.adapter.activity=original_activity.copy()

    def test_revoked_or_different_supervisor_cannot_mutate_any_stage(self):
        self.create();self.verify()
        other=get_user_model().objects.create(username='saud',role='student')
        for actor in [other,self.owner]:
            if actor==self.owner:
                get_user_model().objects.filter(pk=actor.pk).update(is_active=False)
            for operation in [create_candidate,verify_candidate,commit_binding,rollback]:
                with self.assertRaises(RenewalError):operation(self.row.pk,actor,_test_adapter=self.adapter)

    def test_stale_or_busy_source_is_rejected_before_create(self):
        self.adapter.states[self.old]='active'
        with self.assertRaises(RenewalError):self.create()
        self.assertEqual(self.adapter.calls,0)

    def test_ambiguous_rollback_can_reconcile_then_cancel_without_second_create(self):
        self.adapter.create_error=TimeoutError()
        self.create()
        self.assertEqual(rollback(self.row.pk,self.owner).state,'rollback_uncertain')
        self.attest()
        self.row=verify_candidate(self.row.pk,self.owner,candidate_thread_id=self.adapter.target,_test_adapter=self.adapter)
        self.assertEqual(rollback(self.row.pk,self.owner,_test_adapter=self.adapter).state,'cancelled')
        self.create();self.assertEqual(self.adapter.calls,1)

    def test_cancel_before_create_releases_source_for_new_authorized_preparation(self):
        rollback(self.row.pk,self.owner)
        other=prepare_request(self.charter.pk,self.owner,key=uuid.uuid4(),handover_id=self.handover.pk)
        self.assertNotEqual(other.pk,self.row.pk)

    def test_returned_creation_id_storage_failure_is_uncertain_not_retried(self):
        from workspace.renewal import _event
        def fail_created(row,state,evidence):
            if state=='created':raise ValueError('receipt save failure')
            return _event(row,state,evidence)
        with patch('workspace.renewal._event',side_effect=fail_created):
            self.assertEqual(self.create().state,'creation_uncertain')
        self.create();self.assertEqual(self.adapter.calls,1)
        self.attest()
        self.assertEqual(verify_candidate(self.row.pk,self.owner,
            candidate_thread_id=self.adapter.target,_test_adapter=self.adapter).state,'verified')

    def test_stale_independent_observation_and_policy_drift_are_rejected(self):
        proof=self.adapter.inspect(self.old)
        proof['observed_at']='2000-01-01T00:00:00+00:00'
        with patch.object(self.adapter,'inspect',return_value=proof),self.assertRaises(RenewalError):self.create()
        self.policy.model='gpt-6.1-sol';self.policy.save()
        with self.assertRaises(RenewalError):self.create()
        self.assertEqual(self.adapter.calls,0)

    def test_same_source_binding_cannot_be_owned_by_two_charters(self):
        SessionCharter.objects.create(project=self.project,policy=self.policy,key='other-role',
            name='other',scope='bounded',rules='rules',thread_id=self.old)
        with self.assertRaises(RenewalError):self.create()
        self.assertEqual(self.adapter.calls,0)

    def test_dispatch_claim_rechecks_reservation_including_enqueue_only(self):
        from io import StringIO
        from django.core.management import call_command
        from execution_context.models import SessionDispatch
        self.create();job=self.job()
        dispatch=SessionDispatch.objects.create(job=job,dispatch_key=uuid.uuid4(),target_charter=self.charter,
            target_thread_id=self.old,category='text',requested_model='gpt-6-luna',requested_thinking='low')
        job.mode='workspace-write';job.save()
        for enqueue_only in [False,True]:
            output=StringIO()
            call_command('session_outbox','claim',dispatch=dispatch.pk,consumer='test',enqueue_only=enqueue_only,stdout=output)
            self.assertEqual(output.getvalue().strip(),'{}')
        dispatch.refresh_from_db();self.assertEqual(dispatch.state,'queued')

    def test_job_claim_rechecks_reservation_after_initial_selection(self):
        from workspace.dispatcher import run_one
        from workspace.job_lanes import available_jobs
        job=self.job();calls=0
        def changed(query):
            nonlocal calls
            calls+=1
            if calls==2:SessionRenewal.objects.filter(pk=self.row.pk).update(state='creating',create_attempted=True)
            return available_jobs(query)
        with patch('workspace.job_lanes.available_jobs',side_effect=changed):
            self.assertIsNone(run_one())
        job.refresh_from_db();self.assertEqual(job.state,'queued')

    def test_extra_candidate_turn_before_confirmation_is_not_a_bootstrap_receipt(self):
        self.create();self.attest()
        self.adapter.activity[self.adapter.target]='different natural task completed'
        with self.assertRaises(RenewalError):
            verify_candidate(self.row.pk,self.owner,_test_adapter=self.adapter)

    def test_external_binding_change_does_not_allow_second_inflight_renewal(self):
        self.create()
        SessionCharter.objects.filter(pk=self.charter.pk).update(thread_id=str(uuid.uuid4()))
        another=prepare_request(self.charter.pk,self.owner,key=uuid.uuid4(),handover_id=self.handover.pk)
        approval=document(self.project,self.charter.key,'second approval','synthetic',{
            **self.approval.evidence,'renewal_key':str(another.key),'source_thread_id':another.source_thread_id})
        with self.assertRaises(RenewalError):
            create_candidate(another.pk,self.owner,approval_id=approval.pk,_test_adapter=self.adapter)
        self.assertEqual(self.adapter.calls,1)


@override_settings(SESSION_RENEWAL_TEST_ADAPTERS=True)
class RenewalRaceTests(RenewalFixture, TransactionTestCase):
    def test_two_claimants_share_one_durable_creation_intent(self):
        entered=Event();release=Event()
        original=self.adapter.create
        def delayed(**kwargs):
            entered.set()
            if not release.wait(10):raise TimeoutError()
            return original(**kwargs)
        self.adapter.create=delayed
        def first():
            close_old_connections()
            try:return self.create().state
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(first)
            self.assertTrue(entered.wait(10))
            try:
                duplicate=create_candidate(self.row.pk,self.owner,approval_id=self.approval.pk,_test_adapter=self.adapter)
                self.assertEqual(duplicate.state,'creating')
            finally:release.set()
            self.assertEqual(future.result(timeout=10),'created')
        self.assertEqual(self.adapter.calls,1)
        self.assertEqual(SessionRenewalEvent.objects.filter(state='creating').count(),1)
