"""Repair transport labels from existing verified evidence without dispatching work."""
from django.db import transaction
from execution_context.models import SessionDispatch, SessionDispatchEvent


@transaction.atomic
def reconcile_transport_history(job_ids):
    ids = set(job_ids)
    if not ids or len(ids) > 100:
        raise ValueError('Select between 1 and 100 existing jobs')
    corrected = []
    skipped = []
    for dispatch in SessionDispatch.objects.select_for_update().filter(job_id__in=ids).select_related('job'):
        job = dispatch.job
        turn = dispatch.turns.filter(turn_key=dispatch.turn_key).first()
        policy_proof = dispatch.events.filter(state='policy_mismatch', detail__turn_id=dispatch.turn_key).exists()
        if (dispatch.state != 'acknowledged' or job.state != 'needs_review' or not turn or
                not turn.source_evidence or not policy_proof or not job.finished_at or not job.result.strip() or
                turn.usage != job.token_usage or turn.model != job.observed_model or
                turn.target_thread_id != dispatch.target_thread_id or
                (turn.model, turn.thinking) == (dispatch.requested_model, dispatch.requested_thinking)):
            skipped.append(job.pk)
            continue
        dispatch.state = 'finished_review'
        dispatch.completed_at = job.finished_at
        dispatch.save(update_fields=['state', 'completed_at', 'updated_at'])
        SessionDispatchEvent.objects.get_or_create(dispatch=dispatch, state='transport_history_reconciled', detail={
            'turn_id': turn.turn_key, 'turn_row': turn.pk, 'from_state': 'acknowledged',
            'policy_accepted': False, 'usage_added_to_total': False,
        })
        corrected.append(job.pk)
    return {'corrected': corrected, 'skipped': skipped}
