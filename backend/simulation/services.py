import hashlib
import json
from dataclasses import replace
from workspace.models import Project
from shadow_hazard.application.simulation import Scenario, catalogue, run
from .models import SimulationRun


def checksum(result):
    return hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def save_run(scenario, user=None):
    result = run(scenario)
    project = Project.objects.order_by('pk').first()
    if project is None:
        raise ValueError('لا يوجد مشروع لحفظ التجربة')
    return SimulationRun.objects.create(project=project, created_by=user, scenario_key=scenario.key,
        title=scenario.title, version=result['version'], config=result['scenario'],
        metrics=result['metrics'], result=result, digest=checksum(result))


def suite(mode='synthetic', seed=1, grid=False):
    scenarios = [replace(s, mode=mode, seed=seed) for s in catalogue()]
    if grid:
        for speed in (3., 6., 9.):
            for pedestrian in (.8, 1.5, 2.2):
                for delay in (0., 2., 5.):
                    scenarios.append(Scenario(key=f'grid-{speed}-{pedestrian}-{delay}',
                        title=f'مقارنة: سيارة {speed}، طالب {pedestrian}، تأخير {delay}',
                        car_speed=speed, pedestrian_speed=pedestrian, pedestrian_delay=delay, mode=mode, seed=seed))
    return scenarios
