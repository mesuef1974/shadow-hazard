"""Repeatable truth -> sensor -> track -> predict -> independent evaluation pipeline."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import math
import random
import platform
from importlib.metadata import version
from shadow_hazard.domain.motion import (BUS, ROUTES, VERSION, GEOMETRY, CROSSWALK_HALF_WIDTH, STOP_LINE_Y, Observation, assess,
                                        hidden, overlap, point, project)
from shadow_hazard.domain import bus_cameras


@dataclass(frozen=True)
class Scenario:
    key: str = 'crossing'
    title: str = 'وصول متقارب خلف الحافلة'
    car_speed: float = 6.
    pedestrian_speed: float = 1.5
    pedestrian_delay: float = 0.
    noise: float = 0.
    latency: float = 0.
    dropout: float = 0.
    confidence: float = 1.
    fault: str = ''
    mode: str = 'synthetic'
    seed: int = 1
    horizon: float = 6.
    duration: int = 20
    margin: float = .2
    reaction_time: float = .8
    braking_deceleration: float = 3.
    pedestrian_behavior: str = 'cross'
    driver_behavior: str = 'respond'
    signals: str = 'off'
    sensor_fault: str = ''
    crossing_side: str = 'right'
    behavior_time: float = 2.
    pause_duration: float = 3.
    # overhead: كاميرا علوية ترى المشهد كله. bus: كاميرتا الحافلة (ADR 0007) مع إكمال المسار
    # بعد الخروج من المجال. bus_no_coast: كاميرتا الحافلة بلا إكمال.
    sensing: str = 'overhead'
    # تغيّر سرعة السيارة قبل أي استجابة للتحذير: من لحظة car_acceleration_start تتجه إلى
    # car_target_speed بمعدل car_acceleration (م/ث²). الهدف السالب يعني لا تغيّر.
    car_target_speed: float = -1.
    car_acceleration: float = 1.
    car_acceleration_start: float = 0.


def catalogue():
    base = Scenario()
    variants = [base]
    for key, title, changes in [
        ('success', 'سيناريو النجاح: استجابة السائق للتنبيه', {'fault': 'driver_response', 'horizon': 2., 'reaction_time': .5}),
        ('late', 'الطالب يصل بعد السيارة', {'pedestrian_delay': 7.}),
        ('early', 'الطالب يعبر قبل السيارة', {'car_speed': 2.}),
        ('standing', 'الطالب واقف', {'pedestrian_speed': 0.}),
        ('parked', 'السيارة متوقفة بعيدًا', {'car_speed': 0.}),
        ('stop', 'الطالب يتوقف قبل العبور', {'fault': 'stop'}),
        ('reverse', 'الطالب يرجع إلى الخلف', {'fault': 'reverse'}),
        ('brake', 'السيارة تتباطأ وتتوقف', {'fault': 'brake'}),
        ('accelerate', 'السيارة تتسارع', {'fault': 'accelerate'}),
        ('away', 'الطالب يكمل على الرصيف', {'fault': 'away'}),
        ('noise', 'ضوضاء في تحديد الموقع', {'noise': .15}),
        ('delay', 'تأخير الرصد', {'latency': .8}),
        ('loss', 'انقطاع كاميرا المشاة', {'fault': 'loss'}),
        ('dropout', 'فقد متقطع للمشاهدات', {'dropout': .35}),
        ('confidence', 'ثقة ضعيفة في الرصد', {'confidence': .3}),
        ('calibration', 'معايرة غير صالحة', {'fault': 'calibration'}),
        ('clock', 'اختلاف توقيت المصدرين', {'fault': 'clock'}),
        ('missing', 'غياب مصدر منذ البداية', {'fault': 'missing'}),
        ('fast', 'سيارة أسرع', {'car_speed': 10.}),
        ('slow', 'طالب أبطأ', {'pedestrian_speed': .6}),
    ]:
        variants.append(replace(base, key=key, title=title, **changes))
    return variants


def validate(s):
    bounds = {'car_speed': (0, 15), 'pedestrian_speed': (0, 4),
              'pedestrian_delay': (0, 10), 'noise': (0, .5), 'latency': (0, 2),
              'dropout': (0, 1), 'confidence': (0, 1), 'horizon': (.5, 10), 'margin': (0, 1),
              'reaction_time': (0, 10), 'braking_deceleration': (.1, 10)}
    bounds.update(behavior_time=(0, 15), pause_duration=(.5, 10))
    bounds.update(car_target_speed=(-1, 15), car_acceleration=(.1, 5), car_acceleration_start=(0, 30))
    for name, (low, high) in bounds.items():
        value = getattr(s, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'قيمة غير صالحة: {name}')
    if s.duration not in (20, 30) or isinstance(s.duration, bool):
        raise ValueError('مدة التجربة يجب أن تكون20أو30ثانية')
    if s.mode not in ('synthetic', 'vision') or type(s.seed) is not int or not 0 <= s.seed <= 1000000:
        raise ValueError('مصدر الرصد أو بذرة التجربة غير صالحة')
    if s.fault not in {v.fault for v in catalogue()}:
        raise ValueError('حالة غير معروفة')
    choices = {'pedestrian_behavior': {'cross', 'obey', 'standing', 'stop', 'pause_crossing', 'reverse', 'run', 'away'},
               'driver_behavior': {'respond', 'ignore', 'slow', 'brake', 'accelerate', 'parked', 'premature'},
               'signals': {'off', 'auto'}, 'crossing_side': {'right', 'left'},
               'sensor_fault': {'', 'loss', 'missing', 'calibration', 'clock'},
               'sensing': {'overhead', 'bus', 'bus_no_coast'}}
    if any(getattr(s, field) not in values for field, values in choices.items()):
        raise ValueError('اختيار سلوك أو إشارة غير صالح')
    if s.key == 'custom' and s.pedestrian_behavior == 'obey' and s.signals == 'off':
        raise ValueError('انتظار الأخضر يتطلب تشغيل إشارات المشاة')


def observed_car_stopped(track, margin):
    position = point(track['route'], track['progress'])
    return not track['direct_motion'] and abs(track['speed']) < .005 and position[1]+2 < STOP_LINE_Y-margin


def run(s):
    validate(s)
    camera = None
    if s.mode == 'vision':
        from shadow_hazard.adapters.vision import MarkerCamera
        camera = MarkerCamera()
    rng = random.Random(s.seed)
    custom = s.key == 'custom'

    def free_speed(t):
        # سرعة السيارة ما لم يستجب سائقها: ثابتة، ثم تتجه خطيًا إلى السرعة المستهدفة وتثبت.
        if s.car_target_speed < 0:
            return s.car_speed
        change = s.car_acceleration*max(0., t-s.car_acceleration_start)
        return (min(s.car_target_speed, s.car_speed+change) if s.car_target_speed >= s.car_speed
                else max(s.car_target_speed, s.car_speed-change))
    bus_sensing = s.sensing != 'overhead'
    sensor_fault = s.sensor_fault if custom else s.fault
    pedestrian_behavior = s.pedestrian_behavior if custom else s.fault
    driver_behavior = s.driver_behavior if custom else ('respond' if s.fault == 'driver_response' else s.fault)
    routes = dict(ROUTES)
    if custom and s.crossing_side == 'left':
        routes['pedestrian'] = ((-1., -4.), (-1., 0.), (10., 0.))
    if pedestrian_behavior == 'away':
        start = routes['pedestrian'][0]
        routes['pedestrian'] = (start, (start[0], 20.))
    progress = {'car': 0., 'pedestrian': 0.}
    route_lengths = {actor: sum(math.dist(a, b) for a, b in zip(route, route[1:])) for actor, route in routes.items()}
    histories = {actor: [] for actor in routes}
    tracks, queue, frames = {}, [], []
    # نبض الكاميرتين (وضع الحافلة): وقت آخر إطار سليم وصل من كل كاميرا، ولو بلا جسم فيه.
    sensors, beats = {}, []
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observation_log = []
    response_alert = None
    brake_start = None
    resume_start = None
    first_resume_start = None
    first_brake_start = None
    first_response_alert = None
    braking_speed = s.car_speed
    response_cycles = []
    signal = 'OFF' if not custom or s.signals == 'off' else 'RED'
    request_active = False
    entered = False
    pause_start = stopped_since = stopped_at = None
    events = []
    previous_signal = signal
    previous_warning = False
    for index in range(int(s.duration*10)+1):
        now = round(index*.1, 2)
        speeds = {'car': free_speed(now), 'pedestrian': s.pedestrian_speed if now >= s.pedestrian_delay else 0.}
        event_time = s.behavior_time if custom else 2
        if pedestrian_behavior == 'stop' and now >= event_time:
            speeds['pedestrian'] = 0.
        if pedestrian_behavior == 'reverse' and now >= event_time:
            speeds['pedestrian'] *= -1
        if custom and pedestrian_behavior == 'standing':
            speeds['pedestrian'] = 0.
        if custom and pedestrian_behavior == 'run' and now >= max(event_time, s.pedestrian_delay):
            speeds['pedestrian'] = min(4., s.pedestrian_speed*2)
        if custom and pedestrian_behavior == 'pause_crossing' and progress['pedestrian'] >= 9 and pause_start is None:
            pause_start = now
        if pause_start is not None and now < pause_start+s.pause_duration:
            speeds['pedestrian'] = 0.
        if driver_behavior == 'brake':
            speeds['car'] = max(0., s.car_speed-2*now)
        if driver_behavior == 'accelerate':
            speeds['car'] = s.car_speed+now*.5
        if custom and driver_behavior == 'parked':
            speeds['car'] = 0.
        if brake_start is not None:
            speeds['car'] = max(0., braking_speed-s.braking_deceleration*max(0., now-brake_start))
        if resume_start is not None:
            speeds['car'] = min(s.car_speed, 1.5*max(0., now-resume_start))
        if custom and driver_behavior == 'slow' and response_alert is not None:
            speeds['car'] = max(s.car_speed*.5, s.car_speed-s.braking_deceleration*max(0., now-response_alert-s.reaction_time))
        if index:
            for actor in routes:
                movement = speeds[actor]*.1
                if actor == 'car' and s.car_target_speed >= 0:
                    movement = (free_speed(now)+free_speed(now-.1))/2*.1
                if actor == 'car' and driver_behavior in ('brake', 'accelerate'):
                    def free_travel(t):
                        if driver_behavior == 'accelerate':
                            return s.car_speed*t+.25*t*t
                        elapsed = min(t, s.car_speed/2)
                        return s.car_speed*elapsed-elapsed*elapsed
                    movement = free_travel(now)-free_travel(now-.1)
                if custom and actor == 'pedestrian' and pedestrian_behavior == 'obey' and not entered and signal != 'GREEN':
                    movement = min(movement, max(0., 4-progress[actor]))
                if custom and actor == 'pedestrian' and pedestrian_behavior == 'pause_crossing' and pause_start is None:
                    movement = min(movement, max(0., 9-progress[actor]))
                if actor == 'car' and brake_start is not None:
                    # Integrate the assumed driver's braking analytically within this interval.
                    def travel(t):
                        braking = min(max(0., t-brake_start), braking_speed/s.braking_deceleration)
                        return braking_speed*min(t, brake_start)+braking_speed*braking-.5*s.braking_deceleration*braking**2
                    movement = travel(now)-travel(now-.1)
                    if resume_start is not None:
                        def resumed_travel(t):
                            elapsed = max(0., t-resume_start)
                            ramp = min(elapsed, s.car_speed/1.5)
                            return .75*ramp*ramp+s.car_speed*max(0., elapsed-ramp)
                        movement = resumed_travel(now)-resumed_travel(now-.1)
                progress[actor] = max(0., progress[actor]+movement)
                progress[actor] = min(route_lengths[actor], progress[actor])
        truth = {actor: point(routes[actor], progress[actor]) for actor in routes}
        if custom and pedestrian_behavior != 'away' and progress['pedestrian'] > 4 and not entered:
            entered = True
            events.append({'time': now, 'event': 'بدء العبور', 'signal': signal})
        previews = {}
        for actor, pos in truth.items():
            missing = (actor == 'pedestrian' and (sensor_fault == 'missing' or sensor_fault == 'loss' and 1 <= now <= 8))
            missing = missing or rng.random() < s.dropout
            # كاميرا الحافلة لا ترى إلا ما في مجالها؛ بعد سحب العشوائية ليبقى تسلسلها ثابتًا.
            if bus_sensing and not missing:
                beats.append((actor, now+(.4 if actor == 'pedestrian' and sensor_fault == 'clock' else 0),
                              now+s.latency, sensor_fault != 'calibration' and s.confidence >= .6))
            in_view = not bus_sensing or bus_cameras.sees(actor, pos)
            missing = missing or not in_view
            measured = (pos[0]+rng.gauss(0, s.noise), pos[1]+rng.gauss(0, s.noise))
            if camera:
                measured, png = camera.capture(actor, measured, not missing, preview=index % 10 == 0)
                if png:
                    previews[actor] = png
            if missing or measured is None:
                continue
            captured = now+(.4 if actor == 'pedestrian' and sensor_fault == 'clock' else 0)
            sensor_id = (bus_cameras.CAMERAS[actor].sensor_id if bus_sensing
                         else 'left-camera' if actor == 'car' else 'right-camera')
            obs = Observation(actor, sensor_id, captured,
                              now+s.latency, measured, s.confidence,
                              'invalid' if sensor_fault == 'calibration' else 'scene-metres-v1',
                              camera.version if camera else 'synthetic-v1')
            queue.append(obs)
        ready = [obs for obs in queue if obs.received <= now+1e-8]
        queue = [obs for obs in queue if obs.received > now+1e-8]
        for obs in ready:
            h = histories[obs.actor]
            distance = project(routes[obs.actor], obs.position)
            h.append((obs.captured, distance))
            h[:] = h[-5:]
            observation_log.append({**asdict(obs), 'captured_utc': (epoch+timedelta(seconds=obs.captured)).isoformat(),
                                    'received_utc': (epoch+timedelta(seconds=obs.received)).isoformat()})
            if len(h) >= 2 and h[-1][0] > h[0][0]:
                tracks[obs.actor] = {'progress': distance, 'speed': (distance-h[0][1])/(obs.captured-h[0][0]),
                                    'direct_motion': abs(distance-h[-2][1]) > 1e-6,
                                    'direct_displacement': distance-h[-2][1],
                                    'time': obs.captured, 'confidence': obs.confidence, 'frame': obs.frame,
                                    'route': routes[obs.actor]}
        for actor, stamp, received, healthy in beats:
            if received <= now+1e-8 and healthy:
                sensors[actor] = stamp
        beats = [beat for beat in beats if beat[2] > now+1e-8]
        if s.sensing == 'bus':
            # ADR 0007 بند 7: غياب الرصد الذي يفسّره خروج الجسم من مجال الكاميرا لا يُسقط
            # المسار، بل يُكمَل بآخر سرعة مقيسة مع حفظ عمره. والغياب داخل المجال عطل يشيخ به.
            arrived = {obs.actor for obs in ready}
            for actor, track in tracks.items():
                expected = point(track['route'], track['progress']+track['speed']*(now-track['time']))
                if actor not in arrived and not bus_cameras.sees(actor, expected):
                    tracks[actor] = {**track, 'progress': track['progress']+track['speed']*.1,
                                     'time': min(now, round(track['time']+.1, 6)), 'coasted': True}
        # جسم رُصد للتو ولم يكتمل مساره ليس غائبًا عن المجال: يبقى «غير معروف» حتى يكتمل.
        empty = {actor: stamp for actor, stamp in sensors.items() if actor in tracks
                 or not (histories[actor] and now-histories[actor][-1][0] <= .5)}
        # وضع إشارة المشاة مجمّد كما هو (ADR 0002)؛ شرط الحجب للنواة فقط.
        prediction = assess(tracks, now, s.horizon, s.margin,
                            occlusion_gate=not (custom and s.signals == 'auto'),
                            sensors=empty if bus_sensing else None)
        # «لا شيء في المجال» حالة سليمة، لكن ما يقرأ المسارين ينتظر وجودهما معًا.
        reliable = prediction['state'] not in ('UNKNOWN', 'DEGRADED', 'NOTHING_IN_VIEW')
        car_passed = (reliable and point(tracks['car']['route'], tracks['car']['progress'])[1]-2 > CROSSWALK_HALF_WIDTH+s.margin
                      and tracks['car']['speed'] >= 0)
        if custom and s.signals == 'auto':
            signal = 'UNAVAILABLE' if not reliable else 'RED'
            if reliable:
                pt, ct = tracks['pedestrian'], tracks['car']
                observed_p = point(pt['route'], pt['progress'])
                crossed = observed_p[0] < -.3-s.margin if s.crossing_side == 'right' else observed_p[0] > 7.3+s.margin
                if pedestrian_behavior != 'away' and 3.8 <= pt['progress'] <= 4.1:
                    request_active = True
                if crossed or (pt['progress'] < 3.5 and pt['speed'] < 0):
                    request_active = False
                # Immediate displacement veto is separate from the smoothed speed.
                car_still = observed_car_stopped(ct, s.margin)
                stopped_since = (now if stopped_since is None else stopped_since) if car_still else None
                if request_active and ((stopped_since is not None and now-stopped_since >= .5) or car_passed):
                    signal = 'GREEN'
            else:
                stopped_since = None
            if signal != previous_signal:
                events.append({'time': now, 'event': 'إشارة المشاة', 'signal': signal})
                previous_signal = signal
        driver_warning = (prediction['state'] == 'HAZARD_SUSPECTED'
                          or (custom and request_active and reliable and not car_passed))
        if custom and driver_warning and not previous_warning:
            events.append({'time': now, 'event': 'تنبيه السائق'})
        previous_warning = driver_warning
        renewed_warning = (driver_behavior == 'respond' and resume_start is not None
                           and now > resume_start and not car_passed)
        if driver_behavior in ('respond', 'premature', 'slow') and (response_alert is None or renewed_warning) and driver_warning:
            response_alert = now
            if first_response_alert is None:
                first_response_alert = now
            if driver_behavior != 'slow':
                braking_speed = speeds['car']
                brake_start = round(now+s.reaction_time, 6)
                resume_start = None
                if first_brake_start is None:
                    first_brake_start = brake_start
                elif custom and brake_start <= s.duration:
                    events.append({'time': brake_start, 'event': 'بداية فرملة جديدة'})
                response_cycles.append({'alert': now, 'brake_start': brake_start, 'stopped': None, 'resume': None})
        # A simulated driver resumes only after a fresh, trusted observation shows
        # the whole pedestrian beyond the far road edge. No future truth is used.
        if (brake_start is not None and resume_start is None and now >= brake_start
                and speeds['car'] == 0 and prediction['state'] == 'CLEAR_WITHIN_SCOPE'):
            pedestrian_track = tracks['pedestrian']
            observed = point(pedestrian_track['route'], pedestrian_track['progress'])
            crossed = observed[0] < -.3-s.margin if not custom or s.crossing_side == 'right' else observed[0] > 7.3+s.margin
            withdrawn = custom and pedestrian_behavior in ('reverse', 'away') and pedestrian_track['progress'] < 3.5
            if crossed or withdrawn:
                resume_start = now
        if (custom or brake_start is not None and now >= brake_start) and speeds['car'] == 0 and stopped_at is None:
            stopped_at = now
        if custom and driver_behavior == 'premature' and stopped_at is not None and now >= stopped_at+1 and resume_start is None:
            resume_start = now
        if response_cycles:
            cycle = response_cycles[-1]
            if now >= cycle['brake_start'] and speeds['car'] == 0 and cycle['stopped'] is None:
                cycle['stopped'] = now
                if custom and len(response_cycles) > 1:
                    events.append({'time': now, 'event': 'توقف السيارة مجددًا'})
            if resume_start is not None and cycle['resume'] is None:
                cycle['resume'] = resume_start
                if first_resume_start is None:
                    first_resume_start = resume_start
                elif custom:
                    events.append({'time': now, 'event': 'استئناف السيارة مجددًا'})
        driver_state = ('waiting' if brake_start is None else 'reaction' if now < brake_start
                        else 'stopped' if speeds['car'] <= 0 else 'braking')
        if resume_start is not None:
            driver_state = 'resuming' if speeds['car'] < s.car_speed else 'continuing'
        elif custom and speeds['car'] == 0:
            driver_state = 'stopped'
        elif custom and driver_behavior == 'slow' and response_alert is not None:
            driver_state = 'reaction' if now < response_alert+s.reaction_time else 'slowing' if speeds['car'] > s.car_speed*.5 else 'reduced_speed'
        elif custom and driver_behavior == 'brake':
            driver_state = 'braking'
        car_finished = progress['car'] >= route_lengths['car']
        if car_finished:
            speeds['car'] = 0.
            driver_state = 'finished'
        hazards_active = response_alert is not None and resume_start is None
        if car_finished:
            hazards_active = False
        frames.append({'time': now, 'truth': truth, 'hidden': hidden((truth['car'][0], truth['car'][1]+1), truth['pedestrian']),
                       'collision': overlap(truth['car'], truth['pedestrian']), 'prediction': prediction,
                       'tracks': {a: {**t, 'age': round(now-t['time'], 3)} for a, t in tracks.items()},
                       'previews': previews, 'driver_state': driver_state,
                       'hazards_active': hazards_active,
                       'hazards_lit': hazards_active and int(round((now-response_alert)*10)) % 10 < 5,
                       'true_car_speed': speeds['car']})
        if bus_sensing:
            frames[-1]['in_view'] = {actor: bus_cameras.sees(actor, pos) for actor, pos in truth.items()}
        if custom:
            frames[-1].update(pedestrian_signal=signal, crossing_request=request_active,
                              car_motion_detected=reliable and tracks['car']['direct_motion'],
                              driver_warning=driver_warning, pedestrian_in_crossing=entered and 4 < progress['pedestrian'] < 12.5)
    # Summary timings describe the first response; every later cycle is retained.
    brake_start = first_brake_start
    resume_start = first_resume_start
    response_alert = first_response_alert
    first = lambda predicate: next((f['time'] for f in frames if predicate(f)), None)
    alert = first(lambda f: f.get('driver_warning', f['prediction']['state'] == 'HAZARD_SUSPECTED'))
    visible = first(lambda f: not f['hidden'])
    collision = first(lambda f: f['collision'])
    comparison = None
    if not custom and s.fault == 'driver_response':
        comparison = run(replace(s, key='crossing', title='المشهد نفسه دون استجابة السائق', fault=''))
    tp = fp = fn = tn = unavailable = intervened = 0
    intervention_start = brake_start
    if custom and driver_behavior == 'slow' and response_alert is not None:
        intervention_start = round(response_alert+s.reaction_time, 6)
    for i, frame in enumerate(frames):
        reference = comparison['frames'] if comparison else frames
        expected = any(f['collision'] for f in reference[i:i+int(s.horizon/.1)+1])
        frame['reference_hazard'] = expected
        if frame['time']+s.horizon > frames[-1]['time']:
            frame['evaluation'] = 'censored'
            continue
        state = frame['prediction']['state']
        if state in ('UNKNOWN', 'DEGRADED'):
            unavailable += 1
            frame['evaluation'] = 'unavailable'
            continue
        if ((comparison and brake_start is not None and frame['time'] >= brake_start) or
                (custom and intervention_start is not None and frame['time']+s.horizon >= intervention_start)):
            frame['evaluation'] = 'driver_intervention'
            intervened += 1
            continue
        warned = state == 'HAZARD_SUSPECTED'
        label = 'tp' if expected and warned else 'fn' if expected else 'fp' if warned else 'tn'
        frame['evaluation'] = label
        tp += label == 'tp'; fp += label == 'fp'; fn += label == 'fn'; tn += label == 'tn'
    success = (comparison is not None and comparison['metrics']['first_collision'] is not None
               and collision is None and alert is not None and visible is not None and alert < visible)
    resumed_sample = detected_after_resume = revoked_after_resume = None
    if custom and resume_start is not None:
        resumed_sample = next((f['time'] for prev, f in zip(frames, frames[1:])
                               if f['time'] > resume_start and math.dist(prev['truth']['car'], f['truth']['car']) > 1e-6), None)
        if resumed_sample is not None:
            detected_after_resume = first(lambda f: f['time'] >= resumed_sample and f['car_motion_detected'])
            had_green = any(f['time'] == resume_start and f['pedestrian_signal'] == 'GREEN' for f in frames)
            if had_green:
                revoked_after_resume = first(lambda f: f['time'] >= resumed_sample and f['pedestrian_signal'] != 'GREEN')
    if custom:
        completion = first(lambda f: pedestrian_behavior != 'away' and
                           (f['truth']['pedestrian'][0] < -.3-s.margin if s.crossing_side == 'right'
                            else f['truth']['pedestrian'][0] > 7.3+s.margin))
        for event, time in [('بداية الفرملة', 0. if driver_behavior == 'brake' else intervention_start), ('توقف السيارة', stopped_at), ('استئناف السيارة', resume_start),
                            ('تداخل فعلي', collision), ('اكتمال العبور', completion), ('توقف الطالب داخل الممر', pause_start)]:
            if time is not None and time <= s.duration:
                events.append({'time': round(time, 2), 'event': event})
        for event, time in [('أول عينة حركة بعد الاستئناف', resumed_sample), ('كشف مباشر للحركة بعد الاستئناف', detected_after_resume), ('سحب الأخضر بعد الاستئناف', revoked_after_resume)]:
            if time is not None:
                events.append({'time': time, 'event': event})
        events.sort(key=lambda e: e['time'])
    sensing_version = f'-{bus_cameras.VERSION}-{s.sensing}' if bus_sensing else ''
    return {'version': (VERSION+'-direct-motion-v2' if custom else VERSION+'-driver-response-v2' if comparison else VERSION)+sensing_version,
            'events': events,
            'response_cycles': response_cycles,
            'driver_policy': {'resume_acceleration': 1.5, 'resume_after': 'fresh pedestrian observation beyond far road edge', 'hazards_until': 'resume'},
            'comparison': comparison, 'scenario': asdict(s), 'routes': routes, 'bus': BUS, 'geometry': GEOMETRY,
            'runtime': {'python': platform.python_version(),
                        'opencv': version('opencv-python-headless') if camera else None,
                        'numpy': version('numpy') if camera else None},
            'sampling': {'observation_seconds': .1, 'prediction_seconds': .05,
                         'speed_window_samples': 5, 'max_age_seconds': .5,
                         'max_source_skew_seconds': .25, 'min_confidence': .6},
            'units': 'metres-seconds', 'epoch_utc': epoch.isoformat(), 'frames': frames,
            'observations': observation_log, 'metrics': {'first_alert': alert, 'first_visible': visible,
            'first_collision': collision, 'lead_before_visible': round(visible-alert, 2) if visible is not None and alert is not None else None,
            'alert_while_hidden': any(f['hidden'] and f.get('driver_warning', f['prediction']['state'] == 'HAZARD_SUSPECTED') for f in frames),
            'first_prediction_alert': first(lambda f: f['prediction']['state'] == 'HAZARD_SUSPECTED'),
            'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn, 'unavailable': unavailable,
            'intervened': intervened,
            'position_samples': len(observation_log), 'success': success if comparison else None,
            'brake_start': (0. if driver_behavior == 'brake' else intervention_start if intervention_start is not None and intervention_start <= s.duration else None) if custom else brake_start,
            'hazards_start': response_alert,
            'resume_start': resume_start,
            'car_stopped': stopped_at,
            'first_green': first(lambda f: f.get('pedestrian_signal') == 'GREEN'),
            'first_resumed_motion_sample': resumed_sample,
            'motion_detected_after_resume': detected_after_resume,
            'green_revoked_after_resume': revoked_after_resume,
            'motion_detection_delay': round(detected_after_resume-resume_start, 3) if detected_after_resume is not None else None,
            'signal_withdrawal_delay': round(revoked_after_resume-resume_start, 3) if revoked_after_resume is not None else None,
            'sample_motion_detection_delay': round(detected_after_resume-resumed_sample, 3) if detected_after_resume is not None else None,
            'green_while_car_moving_samples': sum(f.get('pedestrian_signal') == 'GREEN' and
                                                f['true_car_speed'] > .1 and f['truth']['car'][1]-2 <= CROSSWALK_HALF_WIDTH+s.margin for f in frames),
            'crossing_complete': completion is not None if custom else None,
            'evaluation_basis': 'without_driver_response' if comparison else 'pre_intervention_only' if custom and intervention_start is not None else 'observed_motion'}}
