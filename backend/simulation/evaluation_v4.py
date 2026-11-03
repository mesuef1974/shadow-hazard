"""Protocol 731, separate from immutable v3 metric definitions."""
import math
from .evaluation import manifest as v3_manifest, contact, occluded, summarize


def manifest():
    rows=[]
    for horizon in (2., 3.):
        for old in v3_manifest():
            config={**old['config'], 'horizon':horizon, 'duration':30}
            pair=f'h{int(horizon)}-{old["pair"]}'
            config['title']=f'P731 {pair} {old["arm"]}'
            rows.append({**old,'pair':pair,'horizon':horizon,'config':config})
    return rows


def audit(result, duration):
    full=result['frames']
    declared=result['scenario']['duration']
    horizon=result['scenario']['horizon']
    if duration not in (20,30) or declared!=30 or horizon not in (2.,3.):
        raise ValueError('Unexpected protocol configuration')
    if len(full)!=301 or any(abs(f['time']-i*.1)>1e-8 for i,f in enumerate(full)):
        raise ValueError('Invalid full sampling grid')
    if {k:list(v) for k,v in result['geometry']['sizes'].items()}!={'car':[1.8,4.],'pedestrian':[.6,.6]}:
        raise ValueError('Unexpected dimensions')
    frames=full[:duration*10+1]
    hits=[];visible=[]
    for f in frames:
        car,ped=f['truth']['car'],f['truth']['pedestrian']
        if not all(math.isfinite(v) for v in [*car,*ped]):raise ValueError('Non-finite truth')
        hit,hidden=contact(car,ped),occluded(car,ped)
        if hit!=f['collision'] or hidden!=f['hidden']:raise ValueError('Truth disagreement')
        if hit:hits.append(f['time'])
        if not hidden:visible.append(f['time'])
    collision=hits[0] if hits else None
    recorded=result['metrics']['first_collision']
    if collision!=(recorded if recorded is not None and recorded<=duration else None):
        raise ValueError('Collision summary disagreement')
    alerts=[f['time'] for f in frames if f['prediction']['state']=='HAZARD_SUSPECTED']
    appropriate=[t for t in alerts if collision is not None and max(0.,collision-horizon)<=t<collision]
    before=[t for t in alerts if collision is not None and t<collision]
    first_visible=visible[0] if visible else None
    actual_lead=first_visible-before[0] if first_visible is not None and before else None
    window_lead=first_visible-appropriate[0] if first_visible is not None and appropriate else None
    return dict(collision=collision,complete=all(math.dist(frames[-1]['truth'][actor],result['routes'][actor][-1])<=1e-6 for actor in ('car','pedestrian')),
        detected=bool(appropriate),hidden_initially=frames[0]['hidden'],lead=actual_lead,
        window_lead=window_lead,first_alert=before[0] if before else None,first_visible=first_visible,
        false_alarm=collision is None and any(t<=duration-horizon for t in alerts),
        unavailable=sum(f['prediction']['state'] in ('UNKNOWN','DEGRADED') for f in frames),
        frame_count=len(frames),late_alerts=sum(t>duration-horizon for t in alerts))


def pair_metrics(a,b,duration):
    ca,cb=dict(a['scenario']),dict(b['scenario'])
    if ca.pop('driver_behavior')!='ignore' or cb.pop('driver_behavior')!='respond':raise ValueError('Arms')
    ca.pop('title');cb.pop('title')
    if ca!=cb or a['routes']!=b['routes']:raise ValueError('Pair config mismatch')
    if len(a['frames'])!=len(b['frames']) or any(x['truth']['pedestrian']!=y['truth']['pedestrian'] for x,y in zip(a['frames'],b['frames'])):
        raise ValueError('Pedestrian trajectory mismatch')
    return {'A':audit(a,duration),'B':audit(b,duration)}


def summarize_cohort(pairs,horizon,duration):
    if any(p.get('horizon')!=horizon or p.get('duration')!=duration for p in pairs):
        raise ValueError('Mixed horizons or duration')
    primary=summarize(pairs)
    secondary=summarize([{**p,'A':{**p['A'],'lead':p['A']['window_lead']}} for p in pairs])
    return dict(primary=primary,window_secondary=secondary)
