"""Audit every catalogue/builder case without adding runs to the project database."""
import json
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from shadow_hazard.application.simulation import Scenario, catalogue, run
from shadow_hazard.domain.motion import overlap, hidden


def audit():
    cases = catalogue()
    for pedestrian in ('cross', 'obey', 'standing', 'stop', 'pause_crossing', 'reverse', 'run', 'away'):
        for driver in ('respond', 'ignore', 'slow', 'brake', 'accelerate', 'parked', 'premature'):
            cases.append(Scenario(key='custom', title=f'{pedestrian}/{driver}', horizon=2,
                                  signals='auto', pedestrian_behavior=pedestrian, driver_behavior=driver))
    rows = []
    for mode in ('synthetic', 'vision'):
        for case in cases:
            result = run(replace(case, mode=mode))
            errors = []
            for scene in (result, result['comparison']):
                if scene is None:
                    continue
                frames = scene['frames']
                for f in frames:
                    car, pupil = f['truth']['car'], f['truth']['pedestrian']
                    if f['collision'] != overlap(car, pupil):
                        errors.append('collision/geometry mismatch')
                    if f['hidden'] != hidden((car[0], car[1]+1), pupil):
                        errors.append('visibility/geometry mismatch')
                    if car == scene['routes']['car'][-1] and (f['true_car_speed'] != 0 or f['driver_state'] != 'finished'):
                        errors.append('terminal car reports movement')
                    if f.get('pedestrian_signal') == 'GREEN' and f['prediction']['state'] in ('UNKNOWN', 'DEGRADED'):
                        errors.append('green with unreliable observations')
                for metric, predicate in (
                    ('first_collision', lambda f: f['collision']),
                    ('first_visible', lambda f: not f['hidden']),
                    ('first_green', lambda f: f.get('pedestrian_signal') == 'GREEN'),
                ):
                    if scene['metrics'][metric] != next((f['time'] for f in frames if predicate(f)), None):
                        errors.append(f'{metric}/frames mismatch')
            rows.append({'mode': mode, 'case': case.title, 'errors': sorted(set(errors)), 'metrics': result['metrics']})
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.local/simulation-audit.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'cases': len(rows), 'failed': sum(bool(r['errors']) for r in rows), 'output': str(output)}, ensure_ascii=False))
    return any(r['errors'] for r in rows)


if __name__ == '__main__':
    sys.exit(audit())
