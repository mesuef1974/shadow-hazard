from django.db import transaction
from django.utils import timezone
from workspace.models import ModelJob,SessionCharter
from workspace.memory import document
from .models import SessionRoutingPolicy
from .routing import validate_manifest
from .readonly_worker import execute
from .readonly_transport import IsolationError


def run_pending(job_id=None):
    """Caller holds the shared worker OS lock; database reservation protects the lane."""
    with transaction.atomic():
        query=ModelJob.objects.filter(mode='read-only',state='waiting_readonly',session_dispatch__isnull=True).order_by('created_at','pk')
        if job_id:query=query.filter(pk=job_id)
        from workspace.job_lanes import available_jobs
        job=available_jobs(query).first()
        if not job:return None
        plan=job.task_context;validate_manifest(plan)
        if not job.owner.is_active or job.owner.role!='supervisor' or not job.charter.policy.enabled:raise IsolationError('تفويض القراءة موقوف')
        target_data=plan.manifest.get('readonly_target',{});policy_data=plan.manifest.get('readonly_policy',{})
        target=SessionCharter.objects.filter(project=job.charter.project,key=target_data.get('key'),policy__enabled=True,
            state__in=['active','prepared','ready_waiting_for_task']).first()
        if not target or target.scope!=target_data.get('scope') or target.rules!=target_data.get('rules'):raise IsolationError('تغير ميثاق القراءة؛ يلزم طلب جديد')
        policy=SessionRoutingPolicy.objects.filter(category=policy_data.get('category'),enabled=True).first()
        if not policy or (job.requested_model,job.requested_thinking)!=(policy_data.get('model'),policy_data.get('thinking')):raise IsolationError('تغيرت سياسة الطلب')
        routing_profile=plan.manifest.get('readonly_routing_profile')
        if routing_profile is not None and routing_profile!={'model':policy.model,'thinking':policy.thinking}:
            raise IsolationError('تغيرت سياسة التوجيه أثناء الانتظار؛ يلزم طلب جديد')
        from workspace.model_limits import model_limit_error
        limit_error=model_limit_error(target,job.requested_model,job.requested_thinking)
        if limit_error:
            job.state='awaiting_approval';job.started_at=None;job.finished_at=None
            job.outcome_note=limit_error
            job.save(update_fields=['state','started_at','finished_at','outcome_note'])
            return None
        if not ModelJob.objects.filter(pk=job.pk,state='waiting_readonly').update(state='running',started_at=timezone.now(),finished_at=None):return None
    try:
        result=execute(job,None)
        with transaction.atomic():
            receipt=document(job.charter.project,target.key,f'readonly-job:{job.pk}',result['text'],
                {'thread_id':result['thread_id'],'turn_id':result['turn_id'],'usage':result['usage'],**result['evidence']})
            needs_review=(any(word in result['text'].casefold() for word in ('تعذرت','لم أتمكن','unable to','cannot access'))
                or any(tool.get('exit_code') not in (0,None) for tool in result['evidence'].get('tool_evidence',[])))
            job.state='needs_review' if needs_review else 'completed';job.result=result['text'];job.token_usage=result['usage']
            job.execution_thread_id=result['thread_id'];job.observed_model=result['model'];job.finished_at=timezone.now()
            job.outcome_note=f'قراءة معزولة بحاوية؛ تحقق التوثيق في قاعدة البيانات: {receipt.pk} / {receipt.digest}'
            job.save(update_fields=['state','result','token_usage','execution_thread_id','observed_model','finished_at','outcome_note'])
            from token_usage.models import TrackedSession
            TrackedSession.objects.get_or_create(external_id=result['thread_id'],defaults={'project':job.charter.project,'name':f'قراءة معزولة · طلب {job.pk}'})
            from .audit import record_context_load,record_readonly_execution
            record_readonly_execution(job,result)
            record_context_load(job,plan,result['thread_id'])
        return job
    except Exception as exc:
        job.state='uncertain';job.outcome_note='لم يثبت اكتمال القراءة المعزولة؛ لا إعادة تلقائية.';job.finished_at=timezone.now()
        job.save(update_fields=['state','outcome_note','finished_at'])
        document(job.charter.project,target.key,f'readonly-job:{job.pk}:failure','فشل مسار القراءة المعزول',{'error_type':type(exc).__name__,'message':str(exc)[:200]})
        raise
