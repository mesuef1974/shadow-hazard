import json
from dataclasses import asdict, replace
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache
from shadow_hazard.application.simulation import Scenario, catalogue, run
from workspace.models import Project
from .models import SimulationRun
from .services import checksum, save_run, suite


def project_runs():
    return SimulationRun.objects.filter(project=Project.objects.order_by('pk').first())


@login_required(login_url="/login/")
@require_GET
@never_cache
def index(request):
    page = Paginator(project_runs(), 100).get_page(request.GET.get('page'))
    return render(request, 'simulation/index.html', {'scenarios': [asdict(s) for s in catalogue()],
                                                   'runs': page, 'page': page})


@login_required(login_url="/login/")
@require_POST
def execute(request, persist=True):
    if request.user.role != 'supervisor':
        return JsonResponse({'error': 'تشغيل التجارب وحفظها متاح للمشرف'}, status=403)
    if len(request.body) > 4096:
        return JsonResponse({'error': 'طلب أكبر من المسموح'}, status=400)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError('طلب غير صالح')
        base = next((s for s in suite(grid=True) if s.key == data.get('key')), None)
        if data.get('key') == 'custom':
            base = Scenario(key='custom', title='تجربة من لوحة التحكم', horizon=2.)
        if base is None:
            raise ValueError('اختر سيناريو معروفًا')
        allowed = {'car_speed', 'pedestrian_speed', 'pedestrian_delay', 'noise', 'latency', 'dropout', 'confidence', 'horizon', 'margin', 'seed', 'mode', 'reaction_time', 'braking_deceleration'}
        allowed |= {'pedestrian_behavior', 'driver_behavior', 'signals', 'sensor_fault', 'crossing_side', 'behavior_time', 'pause_duration'}
        if set(data)-allowed-{'key'}:
            raise ValueError('حقول غير معروفة')
        scenario = replace(base, **{k:v for k,v in data.items() if k in allowed})
        if not persist:
            result = run(scenario)
            return JsonResponse({'id': None, 'digest': checksum(result), 'result': result})
        row = save_run(scenario, request.user)
        return JsonResponse({'id': row.pk, 'digest': row.digest, 'result': row.result})
    except (ValueError, TypeError, ImportError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@login_required(login_url="/login/")
@require_GET
@never_cache
def detail(request, pk):
    row = get_object_or_404(project_runs(), pk=pk)
    if checksum(row.result) != row.digest:
        return JsonResponse({'error': 'تعذر التحقق من سلامة سجل التجربة'}, status=409)
    return JsonResponse({'id': row.pk, 'digest': row.digest, 'result': row.result})
