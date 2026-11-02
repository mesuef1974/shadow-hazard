"""Keep business review history without treating verified finished work as live."""
import uuid

from token_usage.capacity import verified_document
from workspace.models import ModelJob, ProjectDocumentation, SessionCharter


def retired_review_dispatch_ids(project_id):
    from django.db.models import F
    from execution_context.models import SessionDispatch
    candidates = SessionDispatch.objects.filter(target_charter__project_id=project_id,
        state='acknowledged', job__state='needs_review', job__charter=F('target_charter'),
        job__lane=F('target_charter_id'), job__execution_thread_id=F('target_thread_id'),
        turn_key='', turns__isnull=True).exclude(target_thread_id='').exclude(
            target_thread_id__in=SessionCharter.objects.values('thread_id'))
    result=[]
    for row in candidates:
        try:uuid.UUID(row.target_thread_id)
        except (ValueError,TypeError):continue
        result.append(row.pk)
    return result


def finished_review_job_ids(charter):
    result = []
    current_threads = set(SessionCharter.objects.exclude(thread_id='').values_list('thread_id', flat=True))
    jobs = ModelJob.objects.filter(charter=charter, state='needs_review', finished_at__isnull=False).exclude(result='')
    for job in jobs:
        usage = job.token_usage
        if (not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                for k in ['input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens'])
                or usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']
                or usage['cached_input_tokens'] > usage['input_tokens']):
            continue
        dispatch = getattr(job, 'session_dispatch', None)
        if dispatch is not None:
            if dispatch.state != 'finished_review' or not dispatch.turn_key or not dispatch.completed_at:
                continue
            turn = dispatch.turns.filter(turn_key=dispatch.turn_key, target_thread_id=dispatch.target_thread_id,
                usage=usage, model=job.observed_model).first()
            event = dispatch.events.filter(state='policy_mismatch', detail__turn_id=dispatch.turn_key).first()
            receipt = ProjectDocumentation.objects.filter(pk=event.detail.get('receipt')).first() if event else None
            if (turn and receipt and verified_document(receipt) and receipt.project_id == charter.project_id
                    and receipt.session_key == charter.key and receipt.evidence.get('usage') == turn.usage
                    and receipt.evidence.get('turn_id') == turn.turn_key
                    and receipt.evidence.get('source') == turn.source_evidence):
                result.append(job.pk)
        elif job.mode == 'read-only' and job.execution_thread_id not in current_threads:
            # This acceptance review belongs to an exact retired isolated execution.
            # Preserve the review and its old thread; never adopt, resume, or resend it.
            try:
                uuid.UUID(job.execution_thread_id)
            except (ValueError, TypeError):
                continue
            receipt = ProjectDocumentation.objects.filter(project_id=charter.project_id, session_key=charter.key,
                task_key=f'readonly-job:{job.pk}:acceptance-review').first()
            if (receipt and verified_document(receipt) and receipt.evidence.get('job') == job.pk
                    and receipt.evidence.get('result') == job.result):
                result.append(job.pk)
    return result
