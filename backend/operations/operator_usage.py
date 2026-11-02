"""One observational comparison; no model calls, enqueue or billing writes."""
import json
from pathlib import Path
from django.db import transaction
from django.db.models import F
from workspace.models import Project, ProjectDocumentation, ModelJob
from workspace.memory import document
from token_usage.capacity import verified_document
from execution_context.rollout_usage import find_rollout, read_turn_evidence

KEY='operator-usage-comparison:v1'


def metrics(job):
    dispatch=job.session_dispatch
    if job.state!='completed' or dispatch.state!='completed':
        raise ValueError('ينتظر اكتمال الدور بدليل')
    turns=list(dispatch.turns.all())
    if len(turns)!=1:raise ValueError('يلزم دور واحد مثبت')
    turn=turns[0]
    if turn.turn_key!=dispatch.turn_key or turn.target_thread_id!=dispatch.target_thread_id:
        raise ValueError('هوية الدور غير متطابقة')
    path=find_rollout(turn.target_thread_id,turn.source_evidence.get('path'))
    intent=dispatch.events.filter(state='owner_call_intent').order_by('-pk').first()
    origin=None
    if intent:
        e=intent.detail
        origin={'thread_id':e.get('caller_thread_id'),'context_id':e.get('context_id'),'call_id':e.get('call_id')}
    proved=read_turn_evidence(path,thread_id=turn.target_thread_id,claimed_at=dispatch.claimed_at,
        acknowledged_at=dispatch.acknowledged_at,expected_model=dispatch.requested_model,
        expected_thinking=dispatch.requested_thinking,turn_id=turn.turn_key,
        expected_marker=job.task_context.manifest.get('task_marker',''),
        expected_prompt=job.prompt,task_id=job.pk,expected_owner_origin=origin)
    if proved['usage']!=turn.usage or turn.usage!=job.token_usage:
        raise ValueError('استهلاك السجل لا يطابق الدور والطلب')
    proof=proved['evidence'];calls={}
    with Path(path).open(encoding='utf-8') as source:
        for line_no,line in enumerate(source,1):
            if line_no>proof['complete_line']:break
            if line_no<=proof['start_line']:continue
            row=json.loads(line);payload=row.get('payload') or {}
            if row.get('type')!='response_item' or payload.get('type') not in {'function_call','custom_tool_call'}:continue
            call_id=payload.get('call_id') or payload.get('id')
            if not call_id or call_id in calls:raise ValueError('هوية استدعاء أداة مفقودة أو مكررة')
            calls[call_id]=payload.get('name','unknown')
    usage=proved['usage']
    return {'job_id':job.pk,'thread_id':turn.target_thread_id,'turn_id':turn.turn_key,
        'model':turn.model,'thinking':turn.thinking,'usage':usage,
        'tool_calls':len(calls),'tool_names':sorted(set(calls.values())),
        'responses':proof.get('response_count'),
        'noncached_input':usage['input_tokens']-usage['cached_input_tokens'],
        'evidence':proof,'prompt_chars':len(job.prompt)}


def arm(project,baseline_job_id=51):
    previous=ProjectDocumentation.objects.filter(project=project,task_key=KEY+':armed',session_key='session_operator').first()
    if previous:
        if not verified_document(previous):raise ValueError('إيصال الإعداد غير موثوق')
        return previous
    job=ModelJob.objects.get(pk=baseline_job_id,charter__project=project,charter__key='session_operator')
    baseline=metrics(job)
    with transaction.atomic():
        Project.objects.filter(pk=project.pk).update(name=F('name'))
        previous=ProjectDocumentation.objects.filter(project=project,task_key=KEY+':armed',session_key='session_operator').first()
        if previous:
            if not verified_document(previous):raise ValueError('إيصال الإعداد غير موثوق')
            return previous
        return document(project,'session_operator',KEY+':armed',
            'تجهيز مقارنة رصدية تلقائية لأول تنبيه طبيعي بعد التحسين دون تنبيه اختباري.',
            {'baseline':baseline,'optimization_receipt':463,'method':'one natural monitor batch; recorded top-level tool calls only; no token sample writes'})


def collect(project):
    with transaction.atomic():
        Project.objects.filter(pk=project.pk).update(name=F('name'))
        armed=ProjectDocumentation.objects.filter(project=project,session_key='session_operator',task_key=KEY+':armed').first()
        if not armed:return None
        if not verified_document(armed):raise ValueError('إيصال الإعداد غير موثوق')
        existing=ProjectDocumentation.objects.filter(project=project,session_key='session_operator',task_key=KEY+':result').first()
        if existing:
            if not verified_document(existing):raise ValueError('إيصال المقارنة غير موثوق')
            return existing
        selected=ProjectDocumentation.objects.filter(project=project,session_key='session_operator',task_key=KEY+':selected').first()
        if not selected:
            natural=ProjectDocumentation.objects.filter(project=project,session_key='session_operator',
                task_key__startswith='operator-natural-batch:',created_at__gt=armed.created_at).order_by('created_at','pk').first()
            if not natural:return None
            if not verified_document(natural):raise ValueError('مصدر التنبيه غير موثوق')
            selected=document(project,'session_operator',KEY+':selected','تثبيت أول تنبيه طبيعي للمقارنة دون استبداله بنتيجة لاحقة.',
                {'job_id':natural.evidence['job_id'],'natural_receipt':natural.pk,'natural_digest':natural.digest,'armed_receipt':armed.pk})
        if not verified_document(selected):raise ValueError('إيصال الاختيار غير موثوق')
        if selected.evidence['armed_receipt']!=armed.pk:raise ValueError('تغير إعداد المقارنة')
        natural=ProjectDocumentation.objects.get(pk=selected.evidence['natural_receipt'],project=project,session_key='session_operator')
        if not verified_document(natural) or natural.digest!=selected.evidence['natural_digest']:
            raise ValueError('تغير دليل التنبيه الطبيعي')
        if (natural.evidence.get('job_id')!=selected.evidence['job_id']
                or natural.task_key!=f"operator-natural-batch:{selected.evidence['job_id']}"
                or natural.created_at<=armed.created_at):raise ValueError('دليل اختيار التنبيه غير مطابق')
        job=ModelJob.objects.get(pk=selected.evidence['job_id'],charter__project=project,charter__key='session_operator')
        if job.state!='completed':return None
        analysis=ProjectDocumentation.objects.filter(project=project,session_key='session_operator',task_key=f'operator-analysis:{job.pk}').first()
        if not analysis or not verified_document(analysis):return None
        after=metrics(job);before=armed.evidence['baseline']
        delta=after['usage']['total_tokens']-before['usage']['total_tokens']
        result=document(project,'session_operator',KEY+':result','اكتملت مقارنة أول تنبيه طبيعي بعد التحسين من سجلات الدور والتحليل المتحقق.',
            {'before':before,'after':after,'total_delta':delta,
             'change_percent':round(delta/before['usage']['total_tokens']*100,1) if before['usage']['total_tokens'] else None,
             'tools_delta':after['tool_calls']-before['tool_calls'],'analysis_receipt':analysis.pk,
             'armed_receipt':armed.pk,'selection_receipt':selected.pk,
             'caveat':'المرجع اختبار اصطناعي والمهمة الجديدة طبيعية؛ اختلاف المهمة والتاريخ يؤثر في الاستهلاك. ليست وفرة سببية ثابتة.',
             'billing_writes':False})
        if not verified_document(ProjectDocumentation.objects.get(pk=result.pk)):raise ValueError('فشل تحقق المقارنة')
        return result


def snapshot(project):
    rows={row.task_key:row for row in ProjectDocumentation.objects.filter(project=project,
        session_key='session_operator',task_key__in=[KEY+':armed',KEY+':selected',KEY+':result'])}
    armed=rows.get(KEY+':armed')
    if not armed:return None
    if any(not verified_document(row) for row in rows.values()):
        return {'state':'untrusted','message':'دليل المقارنة غير موثوق؛ لا تعرض أرقامًا مؤكدة.'}
    result=rows.get(KEY+':result');selected=rows.get(KEY+':selected')
    data={'state':'completed' if result else 'waiting','before':armed.evidence['baseline'],
        'armed_receipt':armed.pk,'selected_job':selected.evidence['job_id'] if selected else None,
        'message':'بانتظار أول تنبيه طبيعي مكتمل وتحليل موثق؛ لا يُرسل تنبيه للقياس.'}
    if result:data.update(result.evidence);data['result_receipt']=result.pk
    elif selected:
        job=ModelJob.objects.get(pk=selected.evidence['job_id'],charter__project=project)
        data['message']='التنبيه المختار: طلب '+str(job.pk)+' · '+job.state+'؛ ينتظر اكتمال الدليل والتحليل.'
    before=data['before'];after=data.get('after',{})
    data['metrics']=[{'label':label,'before':before['usage'][key],'after':after.get('usage',{}).get(key)}
        for label,key in [('إجمالي التوكنز','total_tokens'),('الإدخال','input_tokens'),('المخزن ضمن الإدخال','cached_input_tokens'),('الإخراج','output_tokens')]]
    data['metrics'] += [{'label':'استدعاءات الأدوات المسجلة','before':before['tool_calls'],'after':after.get('tool_calls')},
        {'label':'الاستجابات النموذجية','before':before['responses'],'after':after.get('responses')}]
    return data
