import csv

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render

from workspace.views import supervisor

from .models import Event
from .services import event_row, filtered_events, snapshot

CSV_LIMIT = 5000
FORMULA = ('=', '+', '-', '@', '\t', '\r')


@login_required(login_url='/login/')
def flow(request):
    if not supervisor(request.user):
        return HttpResponseForbidden()
    return render(request, 'automation_log/flow.html',
                  {'stages': Event.STAGES, 'results': Event.RESULTS, 'sources': Event.SOURCES})


@login_required(login_url='/login/')
def data(request):
    if not supervisor(request.user):
        return HttpResponseForbidden()
    response = JsonResponse(snapshot(request.GET), json_dumps_params={'ensure_ascii': False})
    response['Cache-Control'] = 'private, no-store'
    return response


def _safe(value):
    """خلية تبدأ بصيغة تُنفَّذ في جداول البيانات تُحيَّد."""
    text = str(value)
    return "'" + text if text.startswith(FORMULA) else text


@login_required(login_url='/login/')
def export(request):
    if not supervisor(request.user):
        return HttpResponseForbidden()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="automation-log.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    columns = ['at', 'stage_label', 'result_label', 'source', 'branch', 'sha', 'detail', 'url']
    writer.writerow(['الوقت', 'المرحلة', 'النتيجة', 'المصدر', 'الفرع', 'الالتزام', 'التفصيل', 'الرابط'])
    for event in filtered_events(request.GET)[:CSV_LIMIT]:
        row = event_row(event)
        writer.writerow([_safe(row[name]) for name in columns])
    return response
