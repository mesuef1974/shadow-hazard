import uuid

from django.test import TestCase, override_settings
from django.utils import timezone
from execution_context.models import SessionDispatch, SessionDispatchEvent, SessionDispatchTurn
from workspace.memory import document
from workspace.models import ModelJob
from workspace.renewal import _quiet, RenewalError
from workspace.renewal_history import finished_review_job_ids
from workspace.test_renewal import RenewalFixture


@override_settings(SESSION_RENEWAL_TEST_ADAPTERS=True)
class RenewalHistoryTests(RenewalFixture, TestCase):
    def finished_job(self):
        job = self.job('needs_review')
        job.finished_at = timezone.now()
        job.result = 'review required'
        job.observed_model = 'gpt-6-luna'
        job.token_usage = {'input_tokens':10,'cached_input_tokens':5,'output_tokens':2,'total_tokens':12}
        job.save()
        return job

    def test_verified_finished_policy_review_does_not_occupy_execution_lane(self):
        job = self.finished_job()
        dispatch = SessionDispatch.objects.create(job=job,dispatch_key=uuid.uuid4(),target_charter=self.charter,
            target_thread_id=self.charter.thread_id,state='finished_review',turn_key='done',completed_at=job.finished_at)
        turn=SessionDispatchTurn.objects.create(dispatch=dispatch,target_thread_id=self.charter.thread_id,
            turn_key='done',model=job.observed_model,usage=job.token_usage,source_evidence={'complete_line':100})
        receipt=document(self.project,self.charter.key,'policy-review','policy review',
            {'turn_id':turn.turn_key,'usage':turn.usage,'source':turn.source_evidence})
        SessionDispatchEvent.objects.create(dispatch=dispatch,state='policy_mismatch',detail={'turn_id':'done','receipt':receipt.pk})
        _quiet(self.row)
        job.refresh_from_db();self.assertEqual(job.state,'needs_review')
        dispatch.state='uncertain';dispatch.save(update_fields=['state'])
        with self.assertRaises(RenewalError):_quiet(self.row)

    def test_exact_retired_readonly_acceptance_review_is_retained(self):
        job = self.finished_job()
        job.mode='read-only';job.execution_thread_id=str(uuid.uuid4());job.save()
        document(self.project,self.charter.key,f'readonly-job:{job.pk}:acceptance-review',
            'acceptance failed',{'job':job.pk,'result':job.result})
        self.assertIn(job.pk,finished_review_job_ids(self.charter))
        _quiet(self.row)
        job.execution_thread_id=self.charter.thread_id;job.save(update_fields=['execution_thread_id'])
        with self.assertRaises(RenewalError):_quiet(self.row)

    def test_unproved_or_unfinished_review_still_blocks(self):
        self.finished_job()
        with self.assertRaises(RenewalError):_quiet(self.row)
