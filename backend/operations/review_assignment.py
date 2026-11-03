"""Bounded, idempotent review batches through the existing message outbox."""
import uuid
from django.db import transaction
from workspace.models import ModelJob,SessionCharter
from execution_context.messages import send_message
from .models import ReviewAssignment


@transaction.atomic
def assign_pending_reviews(limit=8):
    if not 1<=limit<=8:raise ValueError('Review batch limit must be between 1 and 8')
    created=[]
    for target in SessionCharter.objects.select_for_update().filter(key='review',policy__enabled=True).order_by('pk'):
        # One outstanding automatic batch per project, including failed/ambiguous
        # reviews: failures need attention, never a recursive stream of new jobs.
        assigned=ReviewAssignment.objects.filter(review_job__charter=target)
        # A completed model turn is insufficient: every original needs its own
        # persisted decision. Telemetry issues do not erase an existing review.
        if any(link.result_document is None for link in assigned.select_related('source_job__charter')):continue
        sender=SessionCharter.objects.filter(project=target.project,key='coordination',policy__enabled=True).first()
        if not sender or not target.thread_id:continue
        # A subquery avoids the nullable reverse join while retaining job/owner/charter locks.
        jobs=list(ModelJob.objects.select_for_update().filter(charter__project=target.project,state='needs_review',
            owner__is_active=True,owner__role='supervisor')
            .exclude(pk__in=ReviewAssignment.objects.values('source_job_id'))
            .exclude(charter=target).exclude(reviewed_assignments__isnull=False).order_by('created_at','pk')[:limit])
        if not jobs:continue
        ids=[job.pk for job in jobs]
        key=uuid.uuid5(uuid.NAMESPACE_URL,f'shadow-auto-review:{target.project_id}:'+','.join(map(str,ids)))
        body=('مراجعة تلقائية مفوضة من سفيان لحالات «يحتاج مراجعة». الطلبات الأصلية: '+','.join(map(str,ids))+'.\n'
            'اقرأ project_memory read --session review ثم افحص لكل طلب ModelJob وSessionDispatch وأحداثه وأدلته وAutomationIssue من قاعدة المشروع. '
            'لا تعِد تشغيل الطلبات ولا تعدل الإنتاج أو البيانات الأصلية ولا تنشئ محادثات أو مهام مراجعة إضافية. '
            'راجع سبب الحالة والنموذج والتفكير والاستهلاك والتوثيق، ثم وثق نتيجة منفصلة لكل طلب عبر project_memory document '
            'بمفتاح automatic-review:JOB_ID مع الأدلة والقرار والتوصية، وأعد ملخصًا يذكر أرقام الطلبات. '
            'إذا نقص الدليل فاذكر العائق صراحة؛ لا تصحح العدادات أو تعتبر الأصل مكتملًا بلا دليل. '
            'الإسناد محفوظ في operations.ReviewAssignment، ونتيجة هذه الدفعة مرتبطة بالأصول تلقائيًا بواسطة review_job.')
        message=send_message(sender=sender,recipient=target,owner=jobs[0].owner,body=body,message_key=key,kind='handoff',task_kind='text')
        for job in jobs:
            ReviewAssignment.objects.create(source_job=job,review_job=message.job)
        created.append({'review_job':message.job_id,'source_jobs':ids})
    return {'created_batches':len(created),'batches':created}
