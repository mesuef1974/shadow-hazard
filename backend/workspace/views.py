from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden
from django.db import transaction, connection
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
import uuid

def supervisor(user): return user.is_authenticated and user.role=='supervisor'

@login_required(login_url='/login/')
def sessions(request):
    if not supervisor(request.user):return HttpResponseForbidden()
    thread_id='01a18e02-0487-7180-9aa0-f932b22c8ef4'
    execution=ExecutionSession.objects.filter(external_key=thread_id).first()
    tracked=TrackedSession.objects.filter(external_id=thread_id,project=project()).first()
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
        'charters':SessionCharter.objects.filter(project=project()).select_related('policy'),
        'coordination_session':coordination_session,
        'routing_policies':SessionRoutingPolicy.objects.order_by('category'),
    })
def project(): return Project.objects.order_by('pk').first()

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
    tasks=Task.objects.filter(phase__project=current).select_related('assignee','phase')
    focus=tasks.filter(state='review') if supervisor(request.user) else tasks.filter(assignee=request.user).exclude(state='accepted')
    decisions=Decision.objects.filter(project=current)
    if not supervisor(request.user):decisions=decisions.filter(private=False)
    return render(request,'workspace/dashboard.html',{'project':current,'focus':focus[:6],'total':tasks.count(),'accepted':tasks.filter(state='accepted').count(),'decisions':decisions[:4]})

@login_required(login_url='/login/')
def phases(request):
    return render(request,'workspace/phases.html',{'phases':Phase.objects.filter(project=project()).prefetch_related('tasks')})

@login_required(login_url='/login/')
def tasks(request):
    items=Task.objects.filter(phase__project=project()).select_related('assignee','phase')
    state=request.GET.get('state','')
    if state:items=items.filter(state=state)
    if request.GET.get('mine'):items=items.filter(assignee=request.user)
    return render(request,'workspace/tasks.html',{'tasks':items,'states':Task.STATES,'selected_state':state})

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
    can_update=supervisor(request.user) or item.assignee_id==request.user.pk and item.state!='accepted'
    form=UpdateForm(request.POST or None,supervisor=supervisor(request.user))
    if request.method=='POST':
        if not can_update:return HttpResponseForbidden('التحديث للمسؤول عن المهمة أو المشرف')
        if form.is_valid():
            with transaction.atomic():
                locked=Task.objects.select_for_update().get(pk=pk)
                if not supervisor(request.user) and (locked.assignee_id!=request.user.pk or locked.state=='accepted'):return HttpResponseForbidden()
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
    return render(request,'workspace/exhibition.html',{'project':project()})


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

        safe_keys = {
            'job_id', 'created_at', 'submission_key', 'owner_id', 'charter_id', 'charter_key',
            'dispatch_id', 'state', 'target_thread_id', 'requested_model', 'requested_thinking',
            'attempt_no', 'max_attempts', 'acknowledged_at', 'turn_keys', 'execution_thread_id',
            'started_at', 'turn_key', 'model', 'observed_model', 'observed_thinking',
            'validated_turn_id', 'turn_count', 'finished_at', 'violations', 'required', 'reason',
            'kind', 'manifest_digest',
        }
        stage_names = (
            ('registration', 'تسجيل'), ('assignment', 'إسناد'), ('dispatch', 'إرسال'),
            ('arrival', 'وصول'), ('execution', 'تنفيذ'), ('verification', 'تحقق'), ('closure', 'إغلاق'),
        )
        for job in ModelJob.objects.select_related('charter').order_by('-created_at')[:40]:
            snapshot = task_snapshot(job)
            stages = []
            for key, label in stage_names:
                stage = snapshot['stages'][key]
                evidence = []
                for record in stage.get('evidence', []):
                    if not isinstance(record, dict):
                        continue
                    evidence.append({name: value for name, value in record.items()
                                     if name in safe_keys and isinstance(value, (str, int, float, bool, list))})
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
                'stages': stages, 'violations': snapshot['violations'],
                'success_verified': snapshot['success_verified'], 'usage_source': usage_source,
                'usage': usage,
                'result': job.result, 'outcome_note': job.outcome_note,
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
        context['heartbeats'] = list(DispatcherHeartbeat.objects.order_by('key'))
    except Exception:
        context['automation_error'] = context['automation_error'] or 'تعذر قراءة حالة موزّع المهام.'

    return render(request, 'workspace/automation.html', context)
