"""Local, serialized Codex resume adapter. No shell command interpolation."""
import shutil
import uuid
from django.conf import settings
from django.db import transaction, IntegrityError
from django.utils import timezone
from .models import ModelJob

MODELS={'gpt-6-luna','gpt-6.1-sol','gpt-6-astra'}
EFFORTS={'low','medium','high'}


def enqueue(charter,owner,prompt,mode='read-only',submission_key=None):
    policy=charter.policy
    uuid.UUID(charter.thread_id)
    if owner.role!='supervisor' or not policy.enabled or policy.model not in MODELS or policy.thinking not in EFFORTS or mode not in {'read-only','workspace-write'}:
        raise ValueError('سياسة أو صلاحية تشغيل غير صالحة')
    if not prompt.strip() or len(prompt)>12000:raise ValueError('المهمة مطلوبة وبحد أقصى 12000 حرف')
    values=dict(charter=charter,owner=owner,prompt=prompt.strip(),requested_model=policy.model,requested_thinking=policy.thinking,mode=mode)
    if submission_key:
        job,created=ModelJob.objects.get_or_create(submission_key=submission_key,defaults=values)
        if not created and (job.owner_id!=owner.pk or job.charter_id!=charter.pk or job.prompt!=prompt.strip() or job.mode!=mode):
            raise ValueError('معرف الطلب مستخدم بمحتوى مختلف')
        return job
    return ModelJob.objects.create(**values)


def run_one():
    if ModelJob.objects.filter(state__in=['running','uncertain']).exists():return None
    job=ModelJob.objects.filter(state='queued').order_by('created_at').first()
    if not job:return None
    try:
        with transaction.atomic():
            claimed=ModelJob.objects.filter(pk=job.pk,state='queued').update(state='running',started_at=timezone.now())
        if not claimed:return None
    except IntegrityError:return None
    job.refresh_from_db()
    try:
        binary=shutil.which('codex.exe') or shutil.which('codex')
        if not binary:raise ValueError('لم يُعثر على Codex محليًا')
        if job.requested_model not in MODELS or job.requested_thinking not in EFFORTS:raise ValueError('نموذج غير مسموح')
        uuid.UUID(job.charter.thread_id)
        if not job.charter.policy.enabled or job.owner.role!='supervisor':raise ValueError('التفويض موقوف')
        prompt=f'مهمة مفوضة من سفيان عبر منصة ظل الخطر. رقم الطلب {job.pk}.\nنطاق الجلسة: {job.charter.scope}\nالميثاق: {job.charter.rules}\nلا ترسل رسائل لجلسات أخرى ولا تنشر أو تدفع بعيدًا. التزم بالمهمة وحدها.\nالمهمة:\n{job.prompt}'
        from .codex_transport import execute, Unavailable, Uncertain
        try:
            response=execute(binary,job.charter.thread_id,job.requested_model,job.requested_thinking,prompt,settings.BASE_DIR.parent,job.mode)
            job.result=response['text']
            job.state='completed' if response['completed'] else 'needs_review'
            job.outcome_note='معرف الدور: '+response['turn_id']+'؛ النموذج أُرسل صراحةً، ولم يتحقق بعد من سجل الاستخدام.'
        except Unavailable:
            job.state='blocked';job.outcome_note='تعذر الاتصال بخادم Codex المحلي أو الجلسة مشغولة؛ لم ترسل المهمة.'
        except Uncertain:
            job.state='uncertain';job.outcome_note='لم يتأكد اكتمال الدور أو ينتظر تفاعلًا داخل Codex؛ لا إعادة تلقائية.'
    except (OSError,ValueError):
        job.state='failed';job.outcome_note='تعذر بدء التشغيل المحلي؛ تحقق من Codex وسياسة الجلسة.'
    finally:
        job.finished_at=timezone.now();job.save(update_fields=['state','result','outcome_note','finished_at'])
    return job
