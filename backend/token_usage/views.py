from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from workspace.views import project
from .models import SyncRun, TrackedSession, CollectorState
from datetime import datetime, timezone as dt_timezone
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .services import summarize, synchronize
from .analytics import analyze


def source_datetime(value):
    try:
        parsed=parse_datetime(value) if isinstance(value,str) else None
        return parsed if parsed and timezone.is_aware(parsed) else None
    except (ValueError,TypeError,OverflowError):return None


@login_required(login_url='/login/')
def dashboard(request):
    if request.user.role!='supervisor':return HttpResponseForbidden('متابعة الاستهلاك للمشرف')
    choices=TrackedSession.objects.filter(project=project())
    chosen=request.GET.get('session','')
    selected=choices.filter(pk=int(chosen)).first() if chosen.isdecimal() and len(chosen)<=18 else None
    session_id=selected.pk if selected else None
    sessions, totals=summarize(project(),session_id)
    period=request.GET.get('period','week')
    if period not in {'all','day','week'}:period='week'
    state=CollectorState.objects.filter(key='local').first()
    health=state.payload if state else {}
    collector=health.get('collector',{}).copy();weekly=health.get('weekly',{}).copy()
    for obj, keys in [(collector,['started_at','completed_at','heartbeat_at','last_success_at']),(weekly,['sampled_at','recorded_at'])]:
        for key in keys:obj[key]=source_datetime(obj.get(key))
    heartbeat=collector.get('heartbeat_at')
    collector['recent']=bool(heartbeat and timezone.is_aware(heartbeat) and 0<=(timezone.now()-heartbeat).total_seconds()<180 and collector.get('status') in {'success','running'})
    try:weekly['reset_date']=datetime.fromtimestamp(weekly['weekly_resets_at'],dt_timezone.utc) if weekly.get('weekly_resets_at') else None
    except (ValueError,TypeError,OverflowError,OSError):weekly['reset_date']=None
    weekly['available']=isinstance(weekly.get('weekly_used_percent'),(float,int)) and 0<=weekly['weekly_used_percent']<=100
    if weekly['available']:weekly['remaining']=round(100-weekly['weekly_used_percent'],1)
    weekly['historical']=not weekly.get('sampled_at') or (timezone.now()-weekly['sampled_at']).total_seconds()>300 or not weekly['reset_date'] or weekly['reset_date']<=timezone.now()
    return render(request,'token_usage/dashboard.html',{'sessions':sessions,'totals':totals,'analytics':analyze(project(),period,session_id),'last_sync':SyncRun.objects.order_by('-started_at').first(),'session_choices':choices,'selected_session':session_id,'collector':collector,'weekly':weekly})


@require_POST
@login_required(login_url='/login/')
def sync(request):
    if request.user.role!='supervisor':return HttpResponseForbidden()
    synchronize()
    return redirect('token-dashboard')
