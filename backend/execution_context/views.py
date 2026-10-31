from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from workspace.models import ModelJob
from workspace.views import project
from .models import EscalationRequest
from .escalation import propose, decide


@login_required(login_url='/login/')
def escalation_list(request):
    if request.user.role!='supervisor':return HttpResponseForbidden()
    requests=EscalationRequest.objects.filter(source_job__charter__project=project()).select_related('source_job','approved_job','decided_by').order_by('-created_at')
    def total(usage):
        if not isinstance(usage,dict) or any(type(usage.get(key)) is not int or usage[key]<0 for key in ('input_tokens','output_tokens')):return None
        return usage['input_tokens']+usage['output_tokens']
    requests=list(requests)
    for item in requests:
        item.source_total=total(item.source_usage)
        item.approved_total=total(item.approved_job.token_usage) if item.approved_job else None
        item.combined_total=item.source_total+item.approved_total if item.source_total is not None and item.approved_total is not None else None
    return render(request,'execution_context/escalations.html',{'requests':requests,'error':request.session.pop('escalation_error','')})


@require_POST
@login_required(login_url='/login/')
def request_escalation(request,job_id):
    if request.user.role!='supervisor':return HttpResponseForbidden()
    job=get_object_or_404(ModelJob,pk=job_id,charter__project=project())
    try:propose(job,request.POST.get('model',''),request.POST.get('reason',''),'supervisor')
    except ValueError as error:request.session['escalation_error']=str(error)
    return redirect('escalations')


@require_POST
@login_required(login_url='/login/')
def decide_escalation(request,pk):
    if request.user.role!='supervisor':return HttpResponseForbidden()
    get_object_or_404(EscalationRequest,pk=pk,source_job__charter__project=project())
    action=request.POST.get('decision')
    if action not in ('approve','reject'):request.session['escalation_error']='قرار غير صالح'
    else:
        try:decide(pk,request.user,action=='approve',request.POST.get('note',''))
        except ValueError as error:request.session['escalation_error']=str(error)
    return redirect('escalations')
