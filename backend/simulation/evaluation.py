"""Protocol 704: sampled, episode-level audit independent of the predictor."""
from dataclasses import asdict
from itertools import product
import math


def manifest():
    from shadow_hazard.application.simulation import Scenario
    cells = [('grid', dict(car_speed=v, pedestrian_speed=p, pedestrian_delay=d))
             for v, p, d in product((3., 6., 9.), (.8, 1.5, 2.2), (0., 2., 5.))]
    cells += [('control', x) for x in (
        {'pedestrian_behavior': 'standing'}, {'pedestrian_behavior': 'away'}, {'car_speed': 0.})]
    rows = []
    for mode in ('synthetic', 'vision'):
        for index, (group, changes) in enumerate(cells):
            pair = f'{mode}-{index:02}'
            for arm, behavior in [('A', 'ignore'), ('B', 'respond')]:
                config = asdict(Scenario(key='custom', title=f'P704 {pair} {arm}',
                    mode=mode, horizon=2., reaction_time=.5, driver_behavior=behavior, **changes))
                rows.append(dict(pair=pair, arm=arm, group=group, config=config))
    return rows


def contact(car, pedestrian):
    # Separating-axis interval test, fixed protocol dimensions; no motion.overlap.
    return all(max(c - a / 2, p - b / 2) <= min(c + a / 2, p + b / 2) + 1e-12
               for c, p, a, b in zip(car, pedestrian, (1.8, 4.), (.6, .6)))


def occluded(car, pedestrian):
    origin = (car[0], car[1] + 1.)
    intervals = [(0., 1.)]
    for start, end, low, high in zip(origin, pedestrian, (4., -12.5), (7., -2.5)):
        delta = end - start
        if abs(delta) < 1e-9:
            if not low <= start <= high:
                return False
        else:
            intervals.append(tuple(sorted(((low-start)/delta, (high-start)/delta))))
    entry = max(a for a, _ in intervals)
    leave = min(b for _, b in intervals)
    return entry <= leave and leave > 0 and entry < 1


def audit(result):
    frames = result['frames']
    if len(frames) != 201 or any(abs(f['time'] - i*.1) > 1e-8 for i, f in enumerate(frames)):
        raise ValueError('Invalid sampling grid')
    if {k: list(v) for k, v in result['geometry']['sizes'].items()} != {'car': [1.8, 4.], 'pedestrian': [.6, .6]}:
        raise ValueError('Unexpected body dimensions')
    hits, visible = [], []
    for f in frames:
        car, ped = f['truth']['car'], f['truth']['pedestrian']
        if not all(math.isfinite(v) for v in [*car, *ped]):
            raise ValueError('Non-finite truth')
        hit, hidden = contact(car, ped), occluded(car, ped)
        if hit != f['collision'] or hidden != f['hidden']:
            raise ValueError(f'Truth disagreement at {f["time"]}')
        if hit:
            hits.append(f['time'])
        if not hidden:
            visible.append(f['time'])
    collision = hits[0] if hits else None
    if result['metrics']['first_collision'] != collision:
        raise ValueError('Collision summary disagrees with truth')
    alerts = [f['time'] for f in frames if f['prediction']['state'] == 'HAZARD_SUSPECTED']
    appropriate = [t for t in alerts if collision is not None and max(0., collision-2.) <= t < collision]
    first_visible = visible[0] if visible else None
    lead = first_visible - appropriate[0] if first_visible is not None and appropriate else None
    complete = all(math.dist(frames[-1]['truth'][actor], result['routes'][actor][-1]) <= 1e-6
                   for actor in ('car', 'pedestrian'))
    return dict(collision=collision, complete=complete, detected=bool(appropriate),
                hidden_initially=frames[0]['hidden'], lead=lead,
                false_alarm=collision is None and any(t <= 18 for t in alerts),
                late_alerts=sum(t > 18 for t in alerts),
                unavailable=sum(f['prediction']['state'] in ('UNKNOWN', 'DEGRADED') for f in frames),
                first_visible=first_visible, first_appropriate_alert=appropriate[0] if appropriate else None)


def pair_metrics(a, b):
    ca, cb = dict(a['scenario']), dict(b['scenario'])
    if ca.pop('driver_behavior') != 'ignore' or cb.pop('driver_behavior') != 'respond':
        raise ValueError('Invalid treatment arms')
    ca.pop('title'); cb.pop('title')
    if ca != cb or a['routes'] != b['routes']:
        raise ValueError('Unmatched pair configuration')
    if any(x['truth']['pedestrian'] != y['truth']['pedestrian'] for x, y in zip(a['frames'], b['frames'])):
        raise ValueError('Pedestrian trajectory changed between arms')
    aa, bb = audit(a), audit(b)
    return {'A': aa, 'B': bb}


def summarize(pairs, *, expected_n=27):
    # Call separately for each sensor mode, grid only. Controls reported separately.
    n = len(pairs)
    if (n != expected_n or len({p.get('pair') for p in pairs}) != n or
            len({p.get('mode') for p in pairs}) != 1 or
            any(p.get('mode') not in ('synthetic', 'vision') or p.get('group') != 'grid' for p in pairs)):
        return dict(n=n, verdict='INVALID', reason='Incomplete, duplicated, or mixed cohort')
    positive = [p for p in pairs if p['A']['collision'] is not None]
    negative = [p for p in pairs if p['A']['collision'] is None]
    hidden = [p for p in positive if p['A']['hidden_initially']]
    def ratio(count, denominator):
        return count / denominator if denominator else None
    avoided = sum(p['B']['collision'] is None and p['B']['complete'] for p in positive)
    counts = {f'{a}{b}': sum((p['A']['collision'] is not None) == bool(a) and
                             (p['B']['collision'] is not None) == bool(b) for p in pairs)
              for a, b in product((0, 1), repeat=2)}
    values = dict(n=n, positive=len(positive), negative=len(negative), hidden=len(hidden),
        counts=counts, effect=ratio(counts['10']-counts['01'], n),
        complete_avoidance=ratio(avoided, len(positive)),
        sensitivity=ratio(sum(p['A']['detected'] for p in positive), len(positive)),
        false_alarm_rate=ratio(sum(p['A']['false_alarm'] for p in negative), len(negative)),
        early_rate=ratio(sum(p['A']['lead'] is not None and p['A']['lead'] >= .2-1e-9 for p in hidden), len(hidden)),
        unfinished_B=sum(not p['B']['complete'] for p in pairs),
        avoided_complete_count=avoided)
    needed = [values[k] for k in ('effect','complete_avoidance','sensitivity','false_alarm_rate','early_rate')]
    values['verdict'] = ('INCONCLUSIVE' if None in needed else
        'PASS' if values['effect'] > 0 and values['complete_avoidance'] >= .8 and
        values['sensitivity'] >= .9 and values['false_alarm_rate'] <= .1 and
        counts['01'] == 0 and values['early_rate'] >= .9 else 'FAIL')
    return values
