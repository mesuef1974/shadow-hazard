from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden
from django.db import transaction, connection
from django.db.models import Prefetch
from .models import Project, Phase, Task, TaskUpdate, Decision
from .forms import TaskForm, UpdateForm, DecisionForm
from .models import SessionCharter, ModelJob, DispatcherHeartbeat, DispatcherRun
from execution_context.models import SessionDispatch, SessionRoutingPolicy
from django.utils import timezone
from datetime import timedelta
from .forms import ModelJobForm
from .dispatcher import enqueue
from registry.models import ExecutionSession
from token_usage.models import TrackedSession
from token_usage.capacity import snapshot as capacity_snapshot
import uuid

def supervisor(user): return user.is_authenticated and user.role=='supervisor'

@login_required(login_url='/login/')
def sessions(request):
    if not supervisor(request.user):return HttpResponseForbidden()
    current_project=project()
    coordination=SessionCharter.objects.filter(project=current_project,key='coordination').first()
    thread_id=coordination.thread_id if coordination else ''
    execution=ExecutionSession.objects.filter(external_key=thread_id).first()
    tracked=TrackedSession.objects.filter(external_id=thread_id,project=current_project).first()
    latest_model_sample=tracked.samples.filter(model__gt='').order_by('-sampled_at','-source_id').first() if tracked else None
    coordination_session=None
    if execution and tracked:
        coordination_session={
            'name':execution.name,
            'thread_id':execution.external_key,
            'client':execution.client,
            'model_sample':latest_model_sample,
        }
    return render(request,'workspace/sessions.html',{
        'capacity_snapshot':capacity_snapshot(current_project),
        'charters':SessionCharter.objects.filter(project=current_project).select_related('policy'),
        'coordination_session':coordination_session,
        'routing_policies':SessionRoutingPolicy.objects.order_by('category'),
    })
def project(): return Project.objects.order_by('pk').first()


@login_required(login_url='/login/')
def automation_live(request):
    """Live status that makes the automation map pulse; code only, zero model tokens."""
    from django.http import JsonResponse
    from .automation_live import snapshot
    if not supervisor(request.user):return HttpResponseForbidden()
    response=JsonResponse(snapshot(),json_dumps_params={'ensure_ascii':False})
    response['Cache-Control']='private, no-store'
    return response


@login_required(login_url='/login/')
def automation_model(request, asset=''):
    """Serve the canonical map and its two assets behind supervisor access."""
    from django.conf import settings
    from django.http import HttpResponse, Http404
    from django.templatetags.static import static
    if not supervisor(request.user):return HttpResponseForbidden()
    root=settings.BASE_DIR.parent/'docs'/'blueprint'
    if asset:
        if asset not in {'automation-map.css','automation-map.js'}:raise Http404
        text=(root/asset).read_text(encoding='utf-8')
        if asset.endswith('.css'):
            text=text.replace('../brand/approved-v1/tokens.css',static('brand/approved-v1/tokens.css'))
        response=HttpResponse(text,content_type='text/css' if asset.endswith('.css') else 'text/javascript')
    else:
        text=(root/'automation-map-ar.html').read_text(encoding='utf-8')
        # Trusted repository content is shared with the local standalone map.
        # Render only its main fragment inside the platform's common shell.
        content=text.split('<main>',1)[1].split('</main>',1)[0]
        content=content.replace('<h1>','<h2 class="model-intro-title">').replace('</h1>','</h2>')
        response=render(request,'workspace/automation_model.html',{'map_content':content,'capacity_snapshot':capacity_snapshot(project())})
    response['Cache-Control']='private, no-cache'
    return response

@login_required(login_url='/login/')
def model_jobs(request):
    if not supervisor(request.user):return HttpResponseForbidden()
    form=ModelJobForm(request.POST or None,initial={'submission_key':uuid.uuid4()})
    form.fields['charter'].queryset=SessionCharter.objects.filter(project=project(),policy__enabled=True).exclude(thread_id='')
    if request.method=='POST' and form.is_valid():
        try:enqueue(form.cleaned_data['charter'],request.user,form.cleaned_data['prompt'],form.cleaned_data['mode'],form.cleaned_data['submission_key'],form.cleaned_data['task_kind'],form.cleaned_data['skills'],form.cleaned_data['plugins'])
        except ValueError as error:form.add_error(None,str(error))
        else:return redirect('model-jobs')
    heartbeat=DispatcherHeartbeat.objects.filter(key='local').first()
    bridge=DispatcherHeartbeat.objects.filter(key='session_bridge').first()
    return render(request,'workspace/model_jobs.html',{'form':form,'jobs':ModelJob.objects.filter(charter__project=project()).select_related('charter')[:30],
        'worker_online':bool(heartbeat and heartbeat.state=='running' and heartbeat.updated_at>timezone.now()-timedelta(seconds=20)),
        'worker_health':heartbeat,'worker_runs':DispatcherRun.objects.order_by('-started_at')[:10],
        'worker_busy':ModelJob.objects.filter(state='running').exists(),
        'worker_uncertain':ModelJob.objects.filter(state='uncertain').exists(),
        'delivery_bridge_online':bool(bridge and bridge.state=='running' and bridge.updated_at>timezone.now()-timedelta(minutes=6)),
        'outbox_queued':SessionDispatch.objects.filter(target_charter__project=project(),state='queued').count(),
        'delivery_uncertain':SessionDispatch.objects.filter(target_charter__project=project(),state='uncertain').count()})

@login_required(login_url='/login/')
def dashboard(request):
    current=project()
    tasks=Task.objects.filter(phase__project=current,owner_only=False).select_related('assignee','phase')
    focus=tasks.filter(state='review') if supervisor(request.user) else tasks.filter(assignee=request.user).exclude(state='accepted')
    decisions=Decision.objects.filter(project=current)
    if not supervisor(request.user):decisions=decisions.filter(private=False)
    return render(request,'workspace/dashboard.html',{'project':current,'focus':focus[:6],'total':tasks.count(),'accepted':tasks.filter(state='accepted').count(),'decisions':decisions[:4]})

@login_required(login_url='/login/')
def phases(request):
    return render(request,'workspace/phases.html',{'phases':Phase.objects.filter(project=project()).prefetch_related(Prefetch('tasks',queryset=Task.objects.filter(owner_only=False)))})

@login_required(login_url='/login/')
def tasks(request):
    owner_tasks=request.GET.get('scope')=='owner'
    if owner_tasks and not supervisor(request.user):return HttpResponseForbidden()
    items=Task.objects.filter(phase__project=project(),owner_only=owner_tasks).select_related('assignee','phase')
    state=request.GET.get('state','')
    if state:items=items.filter(state=state)
    if request.GET.get('mine'):items=items.filter(assignee=request.user)
    return render(request,'workspace/tasks.html',{'tasks':items,'states':Task.STATES,'selected_state':state,'owner_tasks':owner_tasks})

@login_required(login_url='/login/')
def task_create(request):
    if not supervisor(request.user):return HttpResponseForbidden('إنشاء المهام للمشرف فقط')
    form=TaskForm(request.POST or None)
    form.fields['phase'].queryset=Phase.objects.filter(project=project())
    if request.method=='POST' and form.is_valid():
        item=form.save(commit=False);item.creator=request.user;item.save()
        return redirect('task-detail',pk=item.pk)
    return render(request,'workspace/form.html',{'form':form,'title':'مهمة جديدة'})

@login_required(login_url='/login/')
def task_detail(request,pk):
    item=get_object_or_404(Task,pk=pk,phase__project=project())
    if item.owner_only and not supervisor(request.user):return HttpResponseForbidden()
    can_update=supervisor(request.user) or item.assignee_id==request.user.pk and item.state!='accepted'
    form=UpdateForm(request.POST or None,supervisor=supervisor(request.user))
    if request.method=='POST':
        if not can_update:return HttpResponseForbidden('التحديث للمسؤول عن المهمة أو المشرف')
        if form.is_valid():
            with transaction.atomic():
                locked=Task.objects.select_for_update().get(pk=pk)
                if not supervisor(request.user) and (locked.owner_only or locked.assignee_id!=request.user.pk or locked.state=='accepted'):return HttpResponseForbidden()
                update=form.save(commit=False);update.task=locked;update.author=request.user;update.save()
                locked.state=update.state;locked.save(update_fields=['state'])
            return redirect('task-detail',pk=pk)
    return render(request,'workspace/task.html',{'task':item,'form':form,'can_update':can_update,'updates':item.updates.select_related('author')})

@login_required(login_url='/login/')
def decisions(request):
    items=Decision.objects.filter(project=project())
    if not supervisor(request.user):items=items.filter(private=False)
    form=DecisionForm(request.POST or None)
    if request.method=='POST':
        if not supervisor(request.user):return HttpResponseForbidden()
        if project() is None:
            form.add_error(None, 'يجب تهيئة المشروع قبل تسجيل القرارات.')
            return render(request,'workspace/decisions.html',{'decisions':items,'form':form}, status=400)
        if form.is_valid():
            item=form.save(commit=False);item.project=project();item.author=request.user;item.save()
            return redirect('project-decisions')
    return render(request,'workspace/decisions.html',{'decisions':items,'form':form})

@login_required(login_url='/login/')
def exhibition(request):
    if not supervisor(request.user):return HttpResponseForbidden('وضع العرض للمشرف فقط')
    from .models import SessionRenewal
    from token_usage.capacity import verified_document
    current_project=project()
    adoption=SessionRenewal.objects.filter(charter__project=current_project,
        charter__key='coordination',state='manually_adopted').select_related('binding_receipt','charter').order_by('-updated_at').first()
    if adoption and (adoption.charter.thread_id!=adoption.target_thread_id
                     or not adoption.binding_receipt or not verified_document(adoption.binding_receipt)):
        adoption=None
    return render(request,'workspace/exhibition.html',{'project':current_project,'coordination_adoption':adoption})

@login_required(login_url='/login/')
def exhibition_archive(request):
    if not supervisor(request.user):return HttpResponseForbidden('وضع العرض للمشرف فقط')
    return render(request,'workspace/exhibition_archive.html',{'project':project()})

@login_required(login_url='/login/')
def prior_design_archive(request):
    from workspace.models import DocumentAccess
    from simulation.models import SimulationRun
    from .prior_design import PRIOR_DOCUMENTS,PRIOR_RECEIPTS
    documents=list(PRIOR_DOCUMENTS)
    if not supervisor(request.user):
        allowed=set(DocumentAccess.objects.filter(team_visible=True).values_list('path',flat=True))
        documents=[item for item in documents if item[0] in allowed]
    receipts=[(f'__receipts__/{pk}.md',title) for pk,title in PRIOR_RECEIPTS]
    return render(request,'workspace/archive.html',{'runs':SimulationRun.objects.count(),'documents':documents,'receipts':receipts})


AUTOMATION_EVIDENCE_LABELS = {
    'job_id': 'رقم الطلب',
    'created_at': 'وقت التسجيل',
    'submission_key': 'معرّف تقديم الطلب',
    'owner_id': 'معرّف المسؤول',
    'charter_id': 'معرّف الميثاق',
    'charter_key': 'مفتاح الميثاق',
    'dispatch_id': 'معرّف الإرسال',
    'state': 'الحالة',
    'target_thread_id': 'معرّف الجلسة المستهدفة',
    'requested_model': 'النموذج المطلوب',
    'requested_thinking': 'التفكير المطلوب',
    'attempt_no': 'رقم المحاولة',
    'max_attempts': 'الحد الأقصى للمحاولات',
    'acknowledged_at': 'وقت تأكيد الوصول',
    'turn_keys': 'معرّفات الأدوار',
    'execution_thread_id': 'معرّف جلسة التنفيذ',
    'started_at': 'وقت البدء',
    'turn_key': 'معرّف الدور',
    'model': 'النموذج',
    'observed_model': 'النموذج المرصود',
    'observed_thinking': 'التفكير المرصود',
    'validated_turn_id': 'معرّف الدور المتحقق منه',
    'turn_count': 'عدد الأدوار',
    'finished_at': 'وقت الانتهاء',
    'violations': 'المخالفات',
    'required': 'مطلوب',
    'reason': 'السبب',
    'kind': 'النوع',
    'manifest_digest': 'بصمة بيان المهمة',
}


AUTOMATION_VIOLATION_LABELS = {
    'usage_counters_missing_or_invalid': 'عدادات الاستهلاك مفقودة أو غير صالحة',
    'cached_input_exceeds_input': 'المدخلات المخزنة تتجاوز إجمالي المدخلات',
    'reasoning_exceeds_output': 'رموز التفكير تتجاوز المخرجات',
    'usage_total_mismatch': 'إجمالي الاستهلاك غير متطابق',
    'execution_evidence_rejected': 'لم يُعتمد دليل التنفيذ',
    'attempt_number_out_of_range': 'رقم المحاولة خارج الحدود',
    'attempt_event_number_mismatch': 'رقم المحاولة لا يطابق حدثها',
    'attempt_number_evidence_missing': 'دليل رقم المحاولة مفقود',
    'job_thread_mismatch': 'جلسة الطلب غير متطابقة',
    'completed_dispatch_missing_turn_key': 'الإرسال المنتهي يفتقد معرّف الدور',
    'completed_dispatch_missing_turn_evidence': 'الإرسال المنتهي يفتقد دليل الدور',
    'completed_job_missing_observed_model': 'الطلب المنتهي يفتقد النموذج المرصود',
    'completed_job_missing_usage': 'الطلب المنتهي يفتقد الاستهلاك',
    'completed_turn_attempt_count_mismatch': 'عدد الأدوار المنتهية لا يطابق المحاولات',
    'turn_count_exceeds_attempt_number': 'عدد الأدوار يتجاوز عدد المحاولات',
    'turn_key_missing_or_duplicate': 'معرّف الدور مفقود أو مكرر',
    'turn_thread_mismatch': 'جلسة الدور غير متطابقة',
    'turn_model_mismatch': 'نموذج الدور غير متطابق',
    'turn_thinking_mismatch': 'مستوى تفكير الدور غير متطابق',
    'usage_source_reused': 'مصدر الاستهلاك مستخدم أكثر من مرة',
    'dispatch_turn_key_not_in_turns': 'معرّف دور الإرسال غير مسجل بالأدوار',
    'dispatch_job_state_mismatch': 'حالة الإرسال لا تطابق حالة الطلب',
    'job_dispatch_state_mismatch': 'حالة الطلب لا تطابق حالة الإرسال',
    'finished_review_missing_turn_evidence': 'المراجعة المنتهية تفتقد دليل الدور',
    'finished_review_missing_closure_evidence': 'المراجعة المنتهية تفتقد دليل الإغلاق',
    'rejected_dispatch_missing_evidence': 'الإرسال المرفوض يفتقد الدليل',
    'claimed_dispatch_missing_reservation_evidence': 'الإرسال المحجوز يفتقد دليل الحجز',
    'sent_dispatch_missing_sent_at': 'توقيت إرسال الرسالة مفقود',
    'acknowledged_dispatch_missing_arrival_time': 'توقيت الوصول المؤكد مفقود',
    'completed_dispatch_missing_completed_at': 'توقيت انتهاء الإرسال مفقود',
    'observed_model_turn_mismatch': 'النموذج المرصود لا يطابق الدور',
    'requested_observed_model_mismatch': 'النموذج المرصود يخالف المطلوب',
    'requested_observed_thinking_mismatch': 'التفكير المرصود يخالف المطلوب',
    'job_turn_usage_mismatch': 'استهلاك الطلب لا يطابق الدور',
    'observed_thinking_missing': 'مستوى التفكير المرصود مفقود',
    'event_turn_not_recorded': 'دور الحدث غير مسجل',
    'event_recorded_turn_mismatch': 'دور الحدث لا يطابق الدور المسجل',
    'job_observed_model_mismatch': 'النموذج المرصود لا يطابق سجل الطلب',
    'completed_job_missing_closure_evidence': 'الطلب المنتهي يفتقد دليل الإغلاق',
    'local_execution_evidence_missing': 'دليل التنفيذ المحلي مفقود',
    'turn_evidence_missing': 'دليل الدور مفقود',
    'registry_usage_evidence_mismatch': 'دليل الاستهلاك لا يطابق السجل',
    'documented_usage_mismatch': 'الاستهلاك لا يطابق التوثيق',
    'completion_not_verified': 'اكتمال الطلب غير متحقق',
}


def _automation_evidence_value(name, value):
    """Translate enum values for presentation without altering source evidence."""
    if value is None:
        return 'غير مسجل'
    if isinstance(value, bool):
        return 'نعم' if value else 'لا'
    if isinstance(value, list):
        return '، '.join(str(item) for item in value) if value else 'لا يوجد'
    if not isinstance(value, str):
        return value
    if name == 'kind':
        return {'code': 'برمجة', 'design': 'تصميم', 'review': 'مراجعة', 'connected': 'خدمات متصلة',
                'text': 'نصوص', 'calculation': 'حساب', 'frontend': 'واجهة المستخدم',
                'backend': 'الخادم', 'readonly': 'قراءة فقط', 'read-only': 'قراءة فقط',
                'task': 'مهمة', 'reply': 'رد', 'handoff': 'تسليم', 'notice': 'إشعار'}.get(value, value)
    if name == 'reason' and value == 'read-only work is executed by the isolated worker; desktop outbox is not used':
        return 'ينفذ عامل القراءة المعزول هذه المهمة؛ لا يستخدم صندوق إرسال سطح المكتب.'
    if name in {'requested_thinking', 'observed_thinking'}:
        return {'none': 'بلا تفكير', 'minimal': 'أدنى', 'low': 'منخفض',
                'medium': 'متوسط', 'high': 'عالٍ', 'xhigh': 'عالٍ جدًا',
                'max': 'أقصى', 'ultra': 'فائق'}.get(value, value)
    if name == 'state':
        return {
            'queued': 'في الانتظار', 'claimed': 'محجوز للإرسال', 'sent': 'أُرسل',
            'acknowledged': 'تأكد الوصول', 'completed': 'انتهى التنفيذ',
            'finished_review': 'انتهى الدور؛ يحتاج مراجعة السياسة',
            'evidence_rejected': 'انتهى الدور؛ لم يُعتمد الدليل',
            'uncertain': 'غير محسوم', 'running': 'قيد التنفيذ', 'blocked': 'متوقف بعائق',
            'needs_review': 'يحتاج مراجعة', 'failed': 'تعذر التنفيذ',
            'waiting_delivery': 'ينتظر الإرسال أو إثبات الوصول',
            'awaiting_approval': 'ينتظر الموافقة', 'waiting_readonly': 'ينتظر عامل القراءة',
            'stopped': 'متوقف', 'rejected': 'مرفوض', 'cancelled': 'ملغى',
        }.get(value, value)
    return value


def _automation_violation_label(code):
    return AUTOMATION_VIOLATION_LABELS.get(code, "مخالفة غير مصنفة؛ راجع سجل التدقيق")


def _automation_display_text(text):
    """Localize known vocabulary in a display copy of recorded narrative."""
    import re
    labels = {
        **AUTOMATION_VIOLATION_LABELS,
        'FAIL': 'لم يجتز المراجعة', 'BLOCKED': 'معلّق بعائق', 'CONDITIONAL': 'مشروط',
        'PASS': 'اجتاز المراجعة', 'UsageEvent': 'واقعة الاستخدام', 'TokenSample': 'عينة الرموز',
        'evidence_rejected': 'لم يُعتمد الدليل', 'completed': 'انتهى التنفيذ',
        'dispatch': 'الإرسال', 'acknowledged': 'تأكد الوصول', 'rollout': 'سجل التنفيذ',
        'Turn': 'دور التنفيذ', 'turn': 'دور التنفيذ', 'low': 'منخفض', 'medium': 'متوسط',
        'high': 'عالٍ', 'read-only': 'قراءة فقط', 'workspace-write': 'كتابة مساحة العمل',
    }
    pattern = r'(?<![A-Za-z_-])(' + '|'.join(re.escape(key) for key in sorted(labels, key=len, reverse=True)) + r')(?![A-Za-z_-])'
    return re.sub(pattern, lambda match: labels[match.group()], text)


def _automation_reviews(jobs):
    """Batch the result_document lookup contract without per-assignment queries."""
    from operations.models import ReviewAssignment, review_document_query
    from .models import ProjectDocumentation

    assignments = list(ReviewAssignment.objects.filter(source_job_id__in=[job.pk for job in jobs])
                       .values('source_job_id', 'review_job_id'))
    if not assignments:
        return {}
    projects = {job.pk: job.charter.project_id for job in jobs}
    documents = {}
    for doc in ProjectDocumentation.objects.filter(
            review_document_query([item['source_job_id'] for item in assignments]),
            project_id__in=set(projects.values()), session_key='review',
        ).order_by('-created_at', '-pk'):
        documents.setdefault((doc.project_id, int(doc.task_key.split(':')[1])), doc)
    result = {}
    for item in assignments:
        source_id = item['source_job_id']
        doc = documents.get((projects[source_id], source_id))
        decision = doc.evidence.get('decision') if doc and isinstance(doc.evidence, dict) else None
        decision_labels = {'FAIL': 'لم يجتز المراجعة', 'BLOCKED': 'معلّق بعائق',
                           'CONDITIONAL': 'مشروط', 'PASS': 'اجتاز المراجعة'}
        summary_display = doc.summary if doc else ''
        for code, label in decision_labels.items():
            if summary_display.startswith(code + ':'):
                summary_display = label + summary_display[len(code):]
                break
        result[source_id] = {
            'job_id': item['review_job_id'], 'receipt': doc.pk if doc else None,
            'summary': doc.summary if doc else '', 'summary_display': _automation_display_text(summary_display),
            'decision_label': decision_labels.get(decision, 'قرار غير معروف') if isinstance(decision, str) else None,
            'decision': decision if isinstance(decision, str) else None,
        }
    return result


@login_required(login_url='/login/')
def automation(request):
    """Read-only, evidence-led overview of automation and backup operations."""
    if not supervisor(request.user):
        return HttpResponseForbidden()

    context = {
        'jobs': [], 'issues': [], 'backup_runs': [], 'backup_restores': [], 'backup_policy': None,
        'heartbeats': [], 'automation_error': '', 'backup_error': '', 'backup_available': False,
        'backup_supported':connection.vendor=='sqlite',
    }
    try:
        from operations.models import AutomationIssue, BackupPolicy, BackupRun
        from operations.services import task_snapshot

        safe_keys = set(AUTOMATION_EVIDENCE_LABELS)
        stage_names = (
            ('registration', 'تسجيل'), ('assignment', 'إسناد'), ('dispatch', 'إرسال'),
            ('arrival', 'وصول'), ('execution', 'تنفيذ'), ('verification', 'تحقق'), ('closure', 'إغلاق'),
        )
        jobs = list(ModelJob.objects.select_related('charter').order_by('-created_at')[:40])
        reviews = _automation_reviews(jobs)
        from .operational_reviews import operational_reviews
        operational = operational_reviews(jobs)
        for job in jobs:
            snapshot = task_snapshot(job)
            stages = []
            for key, label in stage_names:
                stage = snapshot['stages'][key]
                evidence = []
                for record in stage.get('evidence', []):
                    if not isinstance(record, dict):
                        continue
                    display_record = {name: value for name, value in record.items()
                                      if name in safe_keys and isinstance(value, (str, int, float, bool, list))}
                    if isinstance(display_record.get('violations'), list):
                        display_record['المخالفات'] = '، '.join(_automation_violation_label(code) for code in display_record.pop('violations')) or 'لا توجد مخالفات مسجلة'
                    evidence.append({AUTOMATION_EVIDENCE_LABELS.get(name, name): _automation_evidence_value(name, value)
                                     for name, value in display_record.items()})
                status=stage.get('status','pending')
                status_label={'pending':'بانتظار الدليل','recorded':'مسجل','confirmed':'ثبت بالدليل',
                    'verified':'تم التحقق','failed':'تعذر التحقق','not_required':'غير مطلوب','closed':'مغلق'}.get(status,'بانتظار التحقق')
                stages.append({'label':label,'status':status,'status_label':status_label,'evidence':evidence})
            usage = job.token_usage if isinstance(job.token_usage, dict) else {}
            usage_source=snapshot['usage_source']
            if snapshot['usage_source'] == 'session_dispatch_turn':
                dispatch_records = snapshot['stages']['dispatch'].get('evidence', [])
                turns = dispatch_records[0].get('turns', []) if dispatch_records and isinstance(dispatch_records[0], dict) else []
                usage = turns[-1].get('usage', {}) if turns and isinstance(turns[-1], dict) else {}
                if not turns:usage_source=None
            usage = {key: value for key, value in usage.items()
                     if key in {'input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens'}
                     and type(value) is int and value >= 0}
            context['jobs'].append({
                'id': job.pk, 'state': snapshot['state'], 'created_at': job.created_at,
                'state_label':{'queued':'في الانتظار','waiting_delivery':'ينتظر الإرسال أو إثبات الوصول',
                    'awaiting_approval':'ينتظر الموافقة','waiting_readonly':'ينتظر عامل القراءة',
                    'needs_review':'يحتاج مراجعة','failed':'تعذر التنفيذ','completed':'انتهى التنفيذ — التحقق قيد المراجعة',
                    'running':'قيد التنفيذ','uncertain':'غير محسوم','blocked':'متوقف بعائق'}.get(snapshot['state'],'بانتظار التحقق'),
                'requested': snapshot['requested'], 'observed': snapshot['observed'],
                'requested_thinking_label': _automation_evidence_value('requested_thinking', snapshot['requested'].get('thinking')),
                'observed_thinking_label': _automation_evidence_value('observed_thinking', snapshot['observed'].get('thinking')),
                'usage_display': { {'input_tokens':'رموز المدخلات', 'cached_input_tokens':'رموز المدخلات المخزنة',
                    'output_tokens':'رموز المخرجات', 'reasoning_output_tokens':'رموز التفكير',
                    'total_tokens':'إجمالي الرموز'}[key]: value for key, value in usage.items()},
                'stages': stages, 'violations': snapshot['violations'],
                'violation_labels': [_automation_violation_label(code) for code in snapshot['violations']],
                'success_verified': snapshot['success_verified'], 'usage_source': usage_source,
                'usage': usage,
                'result': _automation_display_text(job.result), 'outcome_note': _automation_display_text(job.outcome_note),
                'review': reviews.get(job.pk),
                'operational_review': operational.get(job.pk),
            })
        context['issues'] = list(AutomationIssue.objects.select_related('job').all()[:60])
    except Exception:
        context['automation_error'] = 'تعذر تحميل سجل دورة المهام. لا تتوفر بيانات موثوقة للعرض الآن.'

    try:
        from operations.models import BackupPolicy, BackupRun
        context['backup_policy'] = BackupPolicy.objects.filter(key='default').first()
        context['backup_runs'] = list(BackupRun.objects.filter(kind__in=['scheduled', 'manual', 'pre_migration']).order_by('-started_at')[:30])
        context['backup_restores'] = list(BackupRun.objects.filter(kind='restore').select_related('source').order_by('-started_at')[:30])
        context['backup_available'] = True
    except Exception:
        context['backup_error'] = 'تعذر قراءة سجل النسخ والاستعادة.'

    try:
        context['heartbeats'] = [
            {'key': row.key, 'state_label': ('غير متاح — النبض متقادم' if row.state == 'running'
             and row.updated_at < timezone.now() - timedelta(minutes=2)
             else _automation_evidence_value('state', row.state)),
             'updated_at': row.updated_at} for row in DispatcherHeartbeat.objects.order_by('key')]
    except Exception:
        context['automation_error'] = context['automation_error'] or 'تعذر قراءة حالة موزّع المهام.'

    context['capacity_snapshot'] = capacity_snapshot(project())
    from operations.operator_usage import snapshot as operator_usage_snapshot
    context['operator_comparison'] = operator_usage_snapshot(project())
    return render(request, 'workspace/automation.html', context)
