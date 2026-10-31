from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden
from django.db import transaction
from .models import Project, Phase, Task, TaskUpdate, Decision
from .forms import TaskForm, UpdateForm, DecisionForm
from .models import SessionCharter

def supervisor(user): return user.is_authenticated and user.role=='supervisor'

@login_required(login_url='/login/')
def sessions(request):
    if not supervisor(request.user):return HttpResponseForbidden()
    return render(request,'workspace/sessions.html',{'charters':SessionCharter.objects.filter(project=project()).select_related('policy')})
def project(): return Project.objects.order_by('pk').first()

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
