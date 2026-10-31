from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from workspace.views import project
from .models import SyncRun
from .services import summarize, synchronize


@login_required(login_url='/login/')
def dashboard(request):
    if request.user.role!='supervisor':return HttpResponseForbidden('متابعة الاستهلاك للمشرف')
    sessions, totals=summarize(project())
    return render(request,'token_usage/dashboard.html',{'sessions':sessions,'totals':totals,'last_sync':SyncRun.objects.order_by('-started_at').first()})


@require_POST
@login_required(login_url='/login/')
def sync(request):
    if request.user.role!='supervisor':return HttpResponseForbidden()
    synchronize()
    return redirect('token-dashboard')
