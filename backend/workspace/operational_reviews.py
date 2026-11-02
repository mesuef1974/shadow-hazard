"""Link independently documented operational checks to their original job."""
from token_usage.capacity import verified_document
from execution_context.models import AuthorityTransferJournal, SessionDispatch, SessionMessage
from .models import ProjectDocumentation, AutoRenewalPolicy
from .auto_renewal import activation_valid


def operational_reviews(jobs):
    projects = {job.pk: job.charter.project_id for job in jobs}
    result = {}
    receipts = ProjectDocumentation.objects.filter(session_key='review',
        evidence__verdict='PASS', evidence__checks__job__in=list(projects)).order_by('-created_at', '-pk')
    for receipt in receipts:
        checks = receipt.evidence.get('checks', {})
        job_id = checks.get('job')
        if job_id in result or projects.get(job_id) != receipt.project_id or not verified_document(receipt):
            continue
        dispatch = SessionDispatch.objects.filter(pk=checks.get('dispatch'), job_id=job_id,
            job__charter__project_id=receipt.project_id, state='completed').first()
        if not dispatch or not dispatch.turns.filter(turn_key=checks.get('turn'),
                target_thread_id=dispatch.target_thread_id).exists():
            continue
        if not SessionMessage.objects.filter(pk=checks.get('message_id_actual'), job_id=job_id).exists():
            continue
        journal = AuthorityTransferJournal.objects.select_related('target').filter(pk=checks.get('journal'),
            project_id=receipt.project_id, state='complete',
            after_snapshot__caller_thread_id=checks.get('caller')).first()
        policy = AutoRenewalPolicy.objects.select_related('owner', 'charter', 'activation_receipt').filter(
            charter__project_id=receipt.project_id, charter__key='coordination',
            activation_receipt_id=checks.get('coordination_activation_receipt')).first()
        if not journal or not policy or not policy.activation_receipt or not verified_document(policy.activation_receipt):
            continue
        result[job_id] = dict(receipt=receipt.pk, decision='PASS', journal=journal.pk,
            target_label=journal.target.name,
            activation_receipt=policy.activation_receipt_id, renewal_valid=activation_valid(policy),
            message=checks['message_id_actual'], dispatch=dispatch.pk,
            summary=receipt.summary, checked_at=receipt.created_at)
    return result
