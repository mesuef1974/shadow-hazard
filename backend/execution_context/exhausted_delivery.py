"""Resolve a proved, exhausted attempt without sending or charging it again."""
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from workspace.memory import document
from workspace.models import ModelJob
from .desktop_session_bridge import BridgeError
from .models import SessionDispatch, SessionDispatchEvent


def record_exhausted(dispatch, evidence):
    """Caller must already have verified the exact attempt's rollout evidence."""
    turn = dispatch.turns.get(turn_key=evidence['turn_id'], target_thread_id=dispatch.target_thread_id)
    if (turn.model != evidence['model'] or turn.thinking != evidence['thinking']
            or turn.usage != evidence['usage'] or 'ESCALATION_REQUEST:' not in evidence['result']):
        raise BridgeError('Exhausted attempt does not match its stored turn')
    receipt = document(dispatch.target_charter.project, dispatch.target_charter.key,
        f'dispatch:{dispatch.pk}:exhausted:{turn.turn_key}',
        'انتهى دور التسليم بدليل موثوق، وتعطل تنفيذ المهمة بعد استنفاد المحاولات؛ لا إعادة إرسال آلية.',
        {'turn_id': turn.turn_key, 'turn_row': turn.pk, 'usage': turn.usage,
         'result': evidence['result'], 'source': evidence['evidence'],
         'business_outcome': 'blocked', 'usage_added_to_total': False})
    dispatch.state = 'finished_review'
    dispatch.turn_key = turn.turn_key
    dispatch.completed_at = dispatch.completed_at or timezone.now()
    dispatch.save(update_fields=['state', 'turn_key', 'completed_at', 'updated_at'])
    ModelJob.objects.filter(pk=dispatch.job_id).update(state='blocked',
        finished_at=dispatch.completed_at, observed_model=turn.model,
        token_usage=turn.usage, result=evidence['result'],
        outcome_note='التسليم وانتهاء الدور مثبتان؛ المهمة متوقفة على المتطلب المسجل وتحتاج عملاً لاحقًا، دون إعادة إرسال أو إضافة الاستهلاك إلى الإجمالي.')
    SessionDispatchEvent.objects.get_or_create(dispatch=dispatch, state='exhaustion_verified',
        detail={'turn_id': turn.turn_key, 'receipt': receipt.pk, 'digest': receipt.digest,
                'business_outcome': 'blocked', 'no_send': True, 'usage_added_to_total': False})
    return receipt


def reconcile_exhausted(dispatch_id, owner):
    from .owner_tools import load_config
    from .owner_delivery import owner_origin_for
    from .rollout_usage import find_rollout, read_turn_evidence
    config = load_config()
    if (not owner.is_active or owner.role != 'supervisor'
            or config['authorizing_user_id'] != owner.pk):
        raise PermissionDenied('Only the delegated supervisor can resolve this history')
    with transaction.atomic():
        SessionDispatch.objects.filter(pk=dispatch_id).update(updated_at=F('updated_at'))
        dispatch = SessionDispatch.objects.select_for_update().select_related('job', 'target_charter').get(pk=dispatch_id)
        if dispatch.target_charter.project_id != config['project_id']:
            raise PermissionDenied('Dispatch is outside the authorized project')
        if (dispatch.state, dispatch.job.state) not in {
                ('uncertain', 'needs_review'), ('finished_review', 'blocked')}:
            raise BridgeError('Only exhausted, explicitly reviewed history can be reconciled')
        stuck = dispatch.events.filter(state='stuck').order_by('-pk').first()
        intent = dispatch.events.filter(state='owner_call_intent').order_by('-pk').first()
        if (not stuck or not intent or intent.detail.get('attempt_no') != dispatch.attempt_no
                or dispatch.attempt_no < dispatch.max_attempts
                or not dispatch.events.filter(state='owner_call_accepted',
                    detail__call_id=intent.detail.get('call_id')).exists()):
            raise BridgeError('Exhaustion or accepted delivery evidence is missing')
        proof = read_turn_evidence(find_rollout(dispatch.target_thread_id),
            thread_id=dispatch.target_thread_id, turn_id=stuck.detail['turn_id'],
            claimed_at=dispatch.claimed_at, acknowledged_at=dispatch.acknowledged_at,
            expected_model=dispatch.requested_model, expected_thinking=dispatch.requested_thinking,
            expected_marker=f'[SHADOW_TASK:{dispatch.job_id}:{dispatch.dispatch_key}:{dispatch.attempt_no}]',
            expected_owner_origin=owner_origin_for(dispatch))
        return record_exhausted(dispatch, proof)
