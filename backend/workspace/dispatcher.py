"""Local serialized job runner with dedicated CLI execution sessions."""
import shutil
import uuid
import os
from pathlib import Path
from django.conf import settings
from django.db import transaction, IntegrityError
from django.utils import timezone
from .models import ModelJob
from execution_context.models import TaskContext
from execution_context.routing import build_plan, calculate, validate_manifest

MODELS={'gpt-6-luna','gpt-6.1-sol','gpt-6-astra'}
EFFORTS={'low','medium','high'}


def resolve_codex():
    binary=shutil.which('codex.exe') or shutil.which('codex')
    if binary:return binary
    # Logon tasks do not inherit Codex desktop's per-process PATH.
    root=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))/'OpenAI'/'Codex'/'bin'
    candidates=list(root.glob('*/codex.exe'))
    return str(max(candidates,key=lambda path:path.stat().st_mtime)) if candidates else None


@transaction.atomic
def enqueue(charter,owner,prompt,mode='read-only',submission_key=None,task_kind='auto',skills=None,plugins=None):
    policy=charter.policy
    uuid.UUID(charter.thread_id)
    if owner.role!='supervisor' or not policy.enabled or policy.model not in MODELS or policy.thinking not in EFFORTS or mode not in {'read-only','workspace-write'}:
        raise ValueError('سياسة أو صلاحية تشغيل غير صالحة')
    if not prompt.strip() or len(prompt)>12000:raise ValueError('المهمة مطلوبة وبحد أقصى 12000 حرف')
    plan=build_plan(charter,prompt,task_kind,skills,plugins,mode)
    task_kind=plan['kind']
    values=dict(charter=charter,owner=owner,prompt=prompt.strip(),requested_model='local-arithmetic' if task_kind=='calculation' else 'gpt-6-luna',requested_thinking='none' if task_kind=='calculation' else 'low',mode=mode)
    if submission_key:
        job,created=ModelJob.objects.get_or_create(submission_key=submission_key,defaults=values)
        if not created and (job.owner_id!=owner.pk or job.charter_id!=charter.pk or job.prompt!=prompt.strip() or job.mode!=mode):
            raise ValueError('معرف الطلب مستخدم بمحتوى مختلف')
        if not created:
            old=TaskContext.objects.filter(job=job).first()
            if not old or any(getattr(old,key)!=plan[key] for key in ('kind','skills','plugins')):raise ValueError('إعداد السياق مختلف عن الطلب السابق')
        else:TaskContext.objects.create(job=job,**plan)
        return job
    job=ModelJob.objects.create(**values)
    TaskContext.objects.create(job=job,**plan)
    return job


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
        if not job.charter.policy.enabled or not job.owner.is_active or job.owner.role!='supervisor':raise ValueError('التفويض موقوف')
        plan=TaskContext.objects.filter(job=job).first()
        if plan:validate_manifest(plan)
        if plan and plan.kind=='calculation':
            if job.mode!='read-only' or plan.skills or plan.plugins:raise ValueError('خطة حساب غير صالحة')
            job.result=calculate(job.prompt)
            job.observed_model='local-arithmetic'
            job.token_usage={'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'reasoning_output_tokens':0}
            job.outcome_note='حساب محلي محدد دون استدعاء نموذج أو أدوات؛ التعليمات والميثاق محفوظان في سجل السياق.'
            from execution_context.audit import record_local_execution
            record_local_execution(job,plan)
            job.state='completed'
            return job
        from execution_context.escalation import authorized
        if not authorized(job):
            job.state='awaiting_approval';job.outcome_note='لم يبدأ التنفيذ: النموذج الأعلى يتطلب موافقة صريحة مرتبطة بهذه المهمة.'
            return job
        binary=resolve_codex()
        if not binary:raise ValueError('لم يُعثر على Codex محليًا')
        if job.requested_model not in MODELS or job.requested_thinking not in EFFORTS:raise ValueError('نموذج غير مسموح')
        uuid.UUID(job.charter.thread_id)
        charter_text=plan.charter if plan else f'نطاق الجلسة: {job.charter.scope}\nالميثاق: {job.charter.rules}'
        prompt=f'مهمة مفوضة من سفيان عبر منصة ظل الخطر. رقم الطلب {job.pk}.\n{charter_text}\nلا ترسل رسائل لجلسات أخرى ولا تنشر أو تدفع بعيدًا. التزم بالمهمة وحدها.\nالمهمة:\n{job.prompt}'
        if plan and plan.skill_text:prompt+='\n\nتعليمات المهارات المطلوبة لهذه المهمة فقط:\n'+plan.skill_text
        prompt+='\nإذا لم تستطع إنجاز المهمة بجودة كافية بالنموذج الحالي، لا تبدل النموذج ولا تشغل مهمة أخرى. أعد فقط ESCALATION_REQUEST: ثم JSON بمفتاحي model وreason؛ اختر gpt-6.1-sol أو gpt-6-astra ووضح العائق المحدد والأدلة ولماذا يفيد النموذج الأعلى. هذا اقتراح ينتظر موافقة سفيان وليس إذنًا بالتنفيذ. لا تطلب التصعيد لمجرد كبر السياق أو غياب أداة.'
        from .codex_transport import execute, Unavailable, Uncertain
        def record_start(thread_id):
            from token_usage.models import TrackedSession
            job.execution_thread_id=thread_id
            job.save(update_fields=['execution_thread_id'])
            TrackedSession.objects.get_or_create(external_id=thread_id,defaults={
                'name':f'{job.charter.name} · طلب {job.pk}','project':job.charter.project})
            from execution_context.audit import record_context_load
            record_context_load(job,plan,thread_id)
        try:
            response=execute(binary,job.charter.thread_id,job.requested_model,job.requested_thinking,prompt,settings.BASE_DIR.parent,job.mode,on_started=record_start,selected_plugins=plan.plugins if plan else [])
            job.result=response['text']
            job.observed_model=response.get('model','')
            job.token_usage=response.get('usage',{})
            job.state='completed' if response['completed'] else 'needs_review'
            if job.observed_model and job.observed_model!=job.requested_model:job.state='needs_review'
            job.outcome_note='تنفيذ مستقل مرتبط بميثاق الجلسة؛ لا يغير نموذج المحادثة المفتوحة. تحقق من النتيجة قبل اعتمادها.'
        except Unavailable:
            job.state='blocked';job.outcome_note='تعذر بدء جلسة التنفيذ المحلية؛ لا إعادة تلقائية.'
        except Uncertain:
            job.state='uncertain';job.outcome_note='لم يتأكد اكتمال الدور أو ينتظر تفاعلًا داخل Codex؛ لا إعادة تلقائية.'
    except (OSError,ValueError):
        job.state='failed';job.outcome_note='تعذر بدء التشغيل المحلي؛ تحقق من Codex وسياسة الجلسة.'
    finally:
        job.finished_at=timezone.now();job.save(update_fields=['state','result','outcome_note','finished_at','observed_model','token_usage'])
    if job.state=='completed':
        from execution_context.escalation import detect_proposal
        detect_proposal(job)
    return job
