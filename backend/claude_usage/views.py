from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import ImportRun, SeatQuotaSnapshot
from .services import summary

FORBIDDEN = 'متابعة الاستهلاك للمشرف'


def _supervisor(request):
    return getattr(request.user, 'role', None) == 'supervisor'


@login_required(login_url='/login/')
def dashboard(request):
    if not _supervisor(request):
        return HttpResponseForbidden(FORBIDDEN)
    context = summary(session_id=request.GET.get('session') or None)
    context.update(last_import=ImportRun.objects.first(), quota=SeatQuotaSnapshot.objects.first(),
                   quota_error=request.GET.get('quota_error') == '1')
    return render(request, 'claude_usage/dashboard.html', context)


def _percent(value, required):
    value = (value or '').strip().replace('٫', '.').rstrip('%٪').strip()
    if not value:
        if required:
            raise InvalidOperation
        return None
    number = Decimal(value)
    if not Decimal('0') <= number <= Decimal('100'):
        raise InvalidOperation
    return number.quantize(Decimal('0.1'))


@login_required(login_url='/login/')
@require_POST
def record_quota(request):
    if not _supervisor(request):
        return HttpResponseForbidden(FORBIDDEN)
    try:
        weekly = _percent(request.POST.get('weekly_percent'), True)
        session = _percent(request.POST.get('session_percent'), False)
    except InvalidOperation:
        return redirect(reverse('claude-usage') + '?quota_error=1')
    SeatQuotaSnapshot.objects.create(taken_at=timezone.now(), weekly_percent=weekly, session_percent=session,
                                     note=(request.POST.get('note') or '')[:200], entered_by=request.user)
    return redirect('claude-usage')
