"""Prepare local calculations and durable outbox records for desktop sessions."""
import uuid
import os
import shutil
from pathlib import Path
from django.db import transaction, IntegrityError
from django.utils import timezone
from .models import ModelJob
from execution_context.models import TaskContext
from execution_context.models import SessionDispatch, SessionDispatchEvent
from execution_context.models import SessionRoutingPolicy
from execution_context.routing import build_plan, calculate, validate_manifest
from .wakeup import signal_dispatcher

MODELS={'gpt-6-luna','gpt-6.1-sol','gpt-6-astra'}
EFFORTS={'low','medium','high'}


def resolve_codex():
    """Legacy diagnostic only; outbox delivery never invokes the standalone CLI."""
    binary=shutil.which('codex.exe') or shutil.which('codex')
    if binary:return binary
    root=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))/'OpenAI'/'Codex'/'bin'
    candidates=list(root.glob('*/codex.exe'))
    return str(max(candidates,key=lambda path:path.stat().st_mtime)) if candidates else None


@transaction.atomic
def enqueue(charter,owner,prompt,mode='read-only',submission_key=None,task_kind='auto',skills=None,plugins=None):
    from .models import SessionCharter
    charter=SessionCharter.objects.select_for_update().select_related('policy','project').get(pk=charter.pk)
    policy=charter.policy
    uuid.UUID(charter.thread_id)
    if not owner.is_active or owner.role!='supervisor' or not policy.enabled or policy.model not in MODELS or policy.thinking not in EFFORTS or mode not in {'read-only','workspace-write'}:
        raise ValueError('سياسة أو صلاحية تشغيل غير صالحة')
    if not prompt.strip() or len(prompt)>12000:raise ValueError('المهمة مطلوبة وبحد أقصى 12000 حرف')
    plan=build_plan(charter,prompt,task_kind,skills,plugins,mode)
    task_kind=plan['kind']
    values=dict(charter=charter,owner=owner,prompt=prompt.strip(),requested_model='local-arithmetic' if task_kind=='calculation' else 'gpt-6-luna',requested_thinking='none' if task_kind=='calculation' else 'low',mode=mode,lane=charter.pk)
    if submission_key:
        job,created=ModelJob.objects.get_or_create(submission_key=submission_key,defaults=values)
        if not created and (job.owner_id!=owner.pk or job.charter_id!=charter.pk or job.prompt!=prompt.strip() or job.mode!=mode):
            raise ValueError('معرف الطلب مستخدم بمحتوى مختلف')
        if not created:
            old=TaskContext.objects.filter(job=job).first()
            if not old or any(getattr(old,key)!=plan[key] for key in ('kind','skills','plugins')):raise ValueError('إعداد السياق مختلف عن الطلب السابق')
        else:
            TaskContext.objects.create(job=job,**plan)
            from workspace.memory import document
            document(charter.project,charter.key,f'job:{job.pk}:memory-load','تحميل ذاكرة المشروع في سياق المهمة',{'job':job.pk,'memory_digest':plan['manifest']['project_memory_digest']})
        transaction.on_commit(signal_dispatcher)
        from execution_context.messages import register_message
        register_message(job)
        return job
    job=ModelJob.objects.create(**values)
    TaskContext.objects.create(job=job,**plan)
    from workspace.memory import document
    document(charter.project,charter.key,f'job:{job.pk}:memory-load','تحميل ذاكرة المشروع في سياق المهمة',{'job':job.pk,'memory_digest':plan['manifest']['project_memory_digest']})
    transaction.on_commit(signal_dispatcher)
    from execution_context.messages import register_message
    register_message(job)
    return job


@transaction.atomic
def run_one():
    from .job_lanes import available_jobs
    job=available_jobs(ModelJob.objects.filter(state='queued')).order_by('created_at','pk').first()
    if not job:return None
    from .models import SessionCharter
    SessionCharter.objects.select_for_update().get(pk=job.charter_id)
    if not available_jobs(ModelJob.objects.filter(pk=job.pk,state='queued')).exists():return None
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
            from workspace.memory import document
            receipt=document(job.charter.project,job.charter.key,f'local-job:{job.pk}',job.result,{'method':'Decimal local arithmetic','usage':job.token_usage})
            job.outcome_note+=f' إيصال توثيق متحقق: {receipt.pk}'
            job.state='completed'
            return job
        from execution_context.escalation import authorized
        if (job.requested_model in {'gpt-6.1-sol','gpt-6-astra'} and not authorized(job)):
            job.state='awaiting_approval';job.outcome_note='لم يبدأ الإرسال: هذا النموذج يتطلب موافقة صريحة مرتبطة بالمهمة.'
            return job
        category=plan.kind if plan else 'text'
        # The owner-selected charter is the explicit destination. Task category
        # chooses model/thinking policy only; it must not silently reroute work.
        target=job.charter
        if (not target.policy.enabled or target.state not in {'active','prepared','ready_waiting_for_task'}
            or not target.thread_id):target=None
        if not target:raise ValueError('الميثاق الذي اختاره المالك غير مفعّل أو غير مهيأ')
        if not target.thread_id:raise ValueError('الجلسة المستهدفة لا تملك معرف محادثة')
        uuid.UUID(target.thread_id)
        route_category='review' if target.key=='review' else category
        routing_policy=SessionRoutingPolicy.objects.filter(category=route_category,enabled=True).first()
        if not routing_policy:raise ValueError('سياسة التوجيه غير موجودة أو معطلة للفئة '+route_category)
        model,thinking=routing_policy.model,routing_policy.thinking
        if job.requested_model in {'gpt-6.1-sol','gpt-6-astra'}:
            model,thinking=job.requested_model,job.requested_thinking
        if model not in MODELS or thinking not in EFFORTS:raise ValueError('سياسة توجيه النموذج غير صالحة')
        if job.mode=='read-only':
            job.requested_model=model;job.requested_thinking=thinking
            job.state='waiting_readonly';job.finished_at=None
            job.outcome_note='ينتظر عامل القراءة المعزول بالحاوية؛ لا يرسل إلى جلسة سطح المكتب.'
            plan.manifest={**plan.manifest,'readonly_target':{'key':target.key,'scope':target.scope,'rules':target.rules},
                'readonly_policy':{'model':model,'thinking':thinking,'category':route_category}}
            plan.save(update_fields=['manifest'])
            return job
        dispatch_key=uuid.uuid4()
        marker=f'[SHADOW_TASK:{job.pk}:{dispatch_key}:1]'
        task_prompt=f'{marker}\nمهمة مفوضة من سفيان. رقم الطلب {job.pk}.\nنطاق الجلسة: {target.scope}\nميثاق الجلسة: {target.rules}\nصلاحية المهمة: {job.mode}. صلاحية read-only لا تسمح بتعديل الملفات أو البيانات. عند التعثر أعد ESCALATION_REQUEST: مع السبب والدليل.\nالمهمة:\n{job.prompt}'
        from workspace.memory import instructions as project_instructions
        shared_memory,memory_digest=project_instructions(job.charter.project,plan.manifest.get('memory_kind','core'))
        from workspace.memory import document
        memory_receipt=document(job.charter.project,job.charter.key,f'job:{job.pk}:memory-load',
            'تحميل ذاكرة المشروع في سياق المهمة',{'job':job.pk,'memory_digest':memory_digest})
        task_prompt+=f'\n\nقواعد القاعدة — إيصال قراءة المضيف {memory_receipt.pk}:\n'+shared_memory
        if plan and plan.skill_text:task_prompt+='\n\nتعليمات المهارات:\n'+plan.skill_text
        route_reason=f'اختار المالك الجلسة {target.name}؛ اختيرت سياسة المهمة {route_category} المحفوظة {model}/{thinking}.'
        with transaction.atomic():
            dispatch=SessionDispatch.objects.create(job=job,dispatch_key=dispatch_key,target_charter=target,
                target_thread_id=target.thread_id,category=route_category,routing_policy=routing_policy,
                max_attempts=routing_policy.max_attempts,requested_model=model,requested_thinking=thinking,
                route_reason=route_reason)
            SessionDispatchEvent.objects.create(dispatch=dispatch,state='queued',detail={'route_reason':route_reason,'attempt':1})
            plan.manifest={**plan.manifest,'dispatch_key':str(dispatch.dispatch_key),'dispatch_prompt':task_prompt,
                'target_thread_id':target.thread_id,'requested_model':model,'requested_thinking':thinking,
                'route_reason':route_reason,'task_marker':marker,
                'target_snapshot':{'thread_id':target.thread_id,'scope':target.scope,'rules':target.rules},
                'policy_snapshot':{name:getattr(routing_policy,name) for name in ('model','thinking','stuck_model','stuck_thinking','max_attempts','max_escalations')},
                'job_snapshot':{'prompt':job.prompt,'mode':job.mode}}
            plan.save(update_fields=['manifest'])
            job.execution_thread_id=target.thread_id
            job.requested_model=model;job.requested_thinking=thinking
            job.state='waiting_delivery';job.outcome_note='أُنشئ سجل إرسال للجلسة القائمة؛ ينتظر الجسر مطالبة الصندوق وإرسال الرسالة.'
            job.finished_at=None
            job.save(update_fields=['state','outcome_note','finished_at','requested_model','requested_thinking','execution_thread_id'])
    except (OSError,ValueError):
        job.state='failed';job.outcome_note='تعذر بدء التشغيل المحلي؛ تحقق من Codex وسياسة الجلسة.'
    finally:
        if job.state not in {'waiting_delivery','waiting_readonly'}:job.finished_at=timezone.now()
        job.save(update_fields=['state','result','outcome_note','finished_at','observed_model','token_usage','requested_model','requested_thinking','execution_thread_id'])
    if job.state=='completed':
        from execution_context.escalation import detect_proposal
        detect_proposal(job)
    return job
