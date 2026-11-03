"""Approval is bound to a frozen task; proposing never queues a higher model."""
import json
import re
from django.db import transaction
from django.utils import timezone
from .models import EscalationRequest, TaskContext

RANK={'gpt-6-luna':0,'gpt-6.1-sol':1,'gpt-6-astra':2}
CONTEXT_FIELDS=('kind','skills','plugins','instructions','charter','skill_text','manifest')


def snapshot(job):
    context=TaskContext.objects.filter(job=job).first()
    return {'prompt':job.prompt,'mode':job.mode,'charter_id':job.charter_id,'owner_id':job.owner_id,
            'context':{key:getattr(context,key) for key in CONTEXT_FIELDS} if context else None}


def propose(job,model,reason,proposed_by='model'):
    if not TaskContext.objects.filter(job=job).exists():
        raise ValueError('المهمة القديمة بلا سياق مثبت؛ أنشئ مهمة جديدة بلونا قبل طلب التصعيد')
    if job.state not in ('completed','needs_review') or not job.finished_at:
        raise ValueError('يلزم انتهاء التنفيذ وحسم حالته قبل التصعيد')
    if model not in RANK or RANK[model]<=RANK.get(job.requested_model,99):raise ValueError('نموذج تصعيد غير صالح')
    reason=reason.strip()
    if not 20<=len(reason)<=2000:raise ValueError('وضح سبب التصعيد في 20 إلى 2000 حرف')
    request,_=EscalationRequest.objects.get_or_create(source_job=job,defaults={
        'target_model':model,'reason':reason,'proposed_by':proposed_by,
        'snapshot':snapshot(job),'source_usage':job.token_usage})
    return request


def detect_proposal(job):
    # A model suggestion is untrusted advice and can only open an approval request.
    match=re.fullmatch(r'ESCALATION_REQUEST:\s*(\{.*\})',job.result.strip(),flags=re.S)
    if not match:return
    job.state='needs_review'
    job.outcome_note='اقتراح تصعيد من النموذج؛ لا يبدأ نموذج أعلى دون قرار المشرف.'
    job.save(update_fields=['state','outcome_note'])
    try:
        data=json.loads(match.group(1))
        if set(data)!={'model','reason'} or not all(isinstance(value,str) for value in data.values()):return
        propose(job,data['model'],data['reason'])
    except (ValueError,TypeError):return


@transaction.atomic
def decide(request_id,actor,approve,note=''):
    from workspace.models import ModelJob
    if not actor.is_active or actor.role!='supervisor':raise ValueError('الموافقة للمشرف فقط')
    # This write obtains the SQLite reservation before any decision reads.
    changed=EscalationRequest.objects.filter(pk=request_id,state='pending').update(state='deciding')
    request=EscalationRequest.objects.select_related('source_job').get(pk=request_id)
    if not changed:return request
    source=request.source_job
    if approve:
        if not request.snapshot.get('context'):raise ValueError('لا يمكن اعتماد تصعيد دون سياق مثبت')
        if snapshot(source)!=request.snapshot:raise ValueError('تغيرت المهمة أو السياق؛ يلزم طلب موافقة جديد')
        if source.state not in ('completed','needs_review') or not source.charter.policy.enabled:raise ValueError('التنفيذ الأصلي غير محسوم أو الجلسة موقوفة')
        if request.target_model not in RANK or RANK[request.target_model]<=RANK.get(source.requested_model,99):raise ValueError('تصعيد غير صالح')
        plan=TaskContext.objects.filter(job=source).first()
        if plan:
            from .routing import validate_manifest
            validate_manifest(plan)
        job=ModelJob.objects.create(charter=source.charter,owner=actor,prompt=source.prompt,mode=source.mode,
            requested_model=request.target_model,requested_thinking=request.target_thinking)
        if plan:TaskContext.objects.create(job=job,**request.snapshot['context'])
        request.approved_job=job
    request.state='approved' if approve else 'rejected'
    request.decided_by=actor;request.decided_at=timezone.now();request.decision_note=note[:2000]
    request.save()
    return request


def authorized(job):
    if job.requested_model=='gpt-6-luna':return True
    request=EscalationRequest.objects.filter(approved_job=job,state='approved',
        target_model=job.requested_model,target_thinking=job.requested_thinking).select_related('decided_by').first()
    if not request or not request.snapshot.get('context') or not request.decided_by or not request.decided_by.is_active or request.decided_by.role!='supervisor':return False
    current=snapshot(job)
    expected={**request.snapshot,'owner_id':request.decided_by_id}
    return current==expected and job.owner_id==request.decided_by_id
