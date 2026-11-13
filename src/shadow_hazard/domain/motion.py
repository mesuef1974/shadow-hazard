"""World-coordinate motion and finite-body conflict; no sensor dependencies."""
from dataclasses import dataclass
from math import hypot

VERSION = 'route-occupancy-v4-analytic-motion-occlusion-gate-v1'
ROUTES = {'car': ((1.75, -36.), (1.75, 20.)),
          'pedestrian': ((8., -4.), (8., 0.), (-3., 0.))}
SIZES = {'car': (1.8, 4.0), 'pedestrian': (.6, .6)}
BUS = (4., -12.5, 7., -2.5)
CROSSWALK_HALF_WIDTH = 1.5
STOP_LINE_Y = -2.5
# عين السائق أمام مركز السيارة بمتر، بالاصطلاح نفسه في المحاكاة والتقييم.
DRIVER_EYE_OFFSET = 1.
GEOMETRY = {'road_edges': (0., 7.), 'lane_divider': 3.5,
            'crosswalk_half_width': CROSSWALK_HALF_WIDTH, 'stop_line_y': STOP_LINE_Y,
            'sizes': SIZES}


def point(route, distance):
    distance = max(0., distance)
    for a, b in zip(route, route[1:]):
        length = hypot(b[0]-a[0], b[1]-a[1])
        if distance <= length:
            return (a[0]+(b[0]-a[0])*distance/length,
                    a[1]+(b[1]-a[1])*distance/length)
        distance -= length
    return route[-1]


def project(route, position):
    best, progress, offset = float('inf'), 0., 0.
    for a, b in zip(route, route[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = hypot(dx, dy)
        fraction = max(0., min(1., ((position[0]-a[0])*dx+(position[1]-a[1])*dy)/length**2))
        error = hypot(position[0]-a[0]-fraction*dx, position[1]-a[1]-fraction*dy)
        if error < best:
            best, progress = error, offset+fraction*length
        offset += length
    return progress


def overlap(car, pedestrian, margin=0.):
    return (abs(car[0]-pedestrian[0]) <= (SIZES['car'][0]+SIZES['pedestrian'][0])/2+margin and
            abs(car[1]-pedestrian[1]) <= (SIZES['car'][1]+SIZES['pedestrian'][1])/2+margin)


def hidden(driver, pedestrian):
    """Segment/rectangle clipping in the shared plan view (not a 3D visibility claim)."""
    low, high = 0., 1.
    for origin, end, minimum, maximum in zip(driver, pedestrian, BUS[:2], BUS[2:]):
        delta = end-origin
        if abs(delta) < 1e-9:
            if not minimum <= origin <= maximum:
                return False
        else:
            enter, leave = sorted(((minimum-origin)/delta, (maximum-origin)/delta))
            low, high = max(low, enter), min(high, leave)
            if low > high:
                return False
    return high > 0 and low < 1


# أقصى معدل نفترضه لتغيّر سرعة السيارة داخل نافذة الوصول (م/ث²).
ENVELOPE_RATE = 2.


def travel(speed, elapsed, factor):
    """مسافة تقطعها السيارة في elapsed إن اتجهت سرعتها إلى factor من المقيسة ثم ثبتت."""
    if speed <= 0 or elapsed <= 0:
        return speed*elapsed
    target = speed*factor
    ramp = min(elapsed, abs(target-speed)/ENVELOPE_RATE)
    rate = ENVELOPE_RATE if target >= speed else -ENVELOPE_RATE
    return speed*ramp+.5*rate*ramp**2+target*(elapsed-ramp)


def travel_bounds(speed, elapsed, envelope):
    """أقل وأكثر مسافة تقطعها السيارة في elapsed إن بقيت سرعتها ضمن ±envelope من المقيسة."""
    return travel(speed, elapsed, 1-envelope), travel(speed, elapsed, 1+envelope)


def swept_overlap(first, last, pedestrian, margin=0.):
    """تداخل الطفل مع كل ما تمسحه السيارة بين موضعين على مسار مستقيم."""
    return all(min(a, b)-(size+SIZES['pedestrian'][axis])/2-margin <= p <=
               max(a, b)+(size+SIZES['pedestrian'][axis])/2+margin
               for axis, (a, b, p, size) in enumerate(zip(first, last, pedestrian, SIZES['car'])))


def window_hazard(tracks, now, horizon, margin, occlusion_gate, envelope):
    """كالتنبؤ الثابت، لكن على كل مسار سرعة ممكن للسيارة ضمن النافذة.

    التحذير يحتاج مسارًا واحدًا يجمع الأمرين معًا: يبلغ الطفل ضمن الأفق، والطفل محجوب عن
    موضع السائق على هذا المسار نفسه الآن. فلا يُخلط خطر مسار بحجب مسار آخر.
    """
    car, walker = tracks['car'], tracks['pedestrian']
    # measured: لحظة آخر قياس حقيقي؛ المسار المكمَّل يتقدم وقته ولا يتجدد قياسه.
    measured = car.get('measured', car['time'])
    origin = car['progress']-car['speed']*(car['time']-measured)
    elapsed = now-measured
    steps = [step*.05 for step in range(int(horizon/.05)+1)]
    walk = [point(walker['route'], walker['progress']+walker['speed']*(now-walker['time']+future))
            for future in steps]

    def swept(index):
        low, high = travel_bounds(car['speed'], elapsed+steps[index], envelope)
        return swept_overlap(point(car['route'], origin+low), point(car['route'], origin+high),
                             walk[index], margin)

    # فحص رخيص أولًا: هل يبلغ أي مسار ممكن الطفل أصلًا؟
    first = next((index for index in range(len(steps)) if swept(index)), None)
    if first is None:
        return {'state': 'CLEAR_WITHIN_SCOPE', 'reason': 'لا تداخل متوقع ضمن أفق الحساب', 'ttc': None}
    low, high = travel_bounds(car['speed'], elapsed+horizon, envelope)
    # مسارات متباعدة مترين على الأكثر عند نهاية الأفق، والسيارة طولها أربعة.
    count = int((high-low)/2)+1
    reached, soonest = False, None
    for sample in range(count+1):
        factor = 1-envelope+2*envelope*sample/count
        for index in range(first, len(steps)):
            position = point(car['route'], origin+travel(car['speed'], elapsed+steps[index], factor))
            if overlap(position, walk[index], margin):
                reached = True
                eye = point(car['route'], origin+travel(car['speed'], elapsed, factor))
                if not occlusion_gate or hidden((eye[0], eye[1]+DRIVER_EYE_OFFSET), walk[0]):
                    soonest = steps[index] if soonest is None else min(soonest, steps[index])
                break
    if soonest is not None:
        return {'state': 'HAZARD_SUSPECTED', 'reason': 'تداخل ممكن ضمن نافذة وصول السيارة', 'ttc': round(soonest, 2)}
    if reached:
        return {'state': 'CLEAR_WITHIN_SCOPE', 'reason': 'السائق يراه: الطفل خارج ظل الحافلة', 'ttc': None}
    return {'state': 'CLEAR_WITHIN_SCOPE', 'reason': 'لا تداخل متوقع ضمن أفق الحساب', 'ttc': None}


@dataclass(frozen=True)
class Observation:
    actor: str
    sensor_id: str
    captured: float
    received: float
    position: tuple
    confidence: float = 1.
    frame: str = 'scene-metres-v1'
    algorithm: str = 'synthetic-v1'


ABSENT = {('car',): 'لا سيارة في مجال الكاميرا', ('pedestrian',): 'لا طفل في مجال الكاميرا',
          ('car', 'pedestrian'): 'لا سيارة ولا طفل في مجال الكاميرتين'}


def assess(tracks, now, horizon=6., margin=.2, occlusion_gate=True, sensors=None,
           speed_envelope=0.):
    """sensors: وقت آخر إطار سليم من كاميرا كل جسم؛ None يعني لا نبض (السلوك القديم)."""
    absent = tuple(actor for actor in ('car', 'pedestrian') if actor not in tracks)
    # جسم بلا مسار عطل، إلا إن كانت كاميرته تنبض الآن: فهي سليمة ولا ترى شيئًا.
    if absent and (sensors is None or any(
            not 0 <= now-sensors.get(actor, float('-inf')) <= .5 for actor in absent)):
        return {'state': 'UNKNOWN', 'reason': 'الرصد غير مكتمل', 'ttc': None}
    for track in tracks.values():
        if (now-track['time'] > .5 or track['time'] > now or
                track['confidence'] < .6 or track['frame'] != 'scene-metres-v1'):
            return {'state': 'DEGRADED', 'reason': 'رصد قديم أو ضعيف أو غير معاير', 'ttc': None}
    if absent:
        return {'state': 'NOTHING_IN_VIEW', 'reason': ABSENT[absent], 'ttc': None}
    if speed_envelope > 0:
        return window_hazard(tracks, now, horizon, margin, occlusion_gate, speed_envelope)
    if abs(tracks['car']['time']-tracks['pedestrian']['time']) > .25:
        return {'state': 'DEGRADED', 'reason': 'الرصد غير متزامن', 'ttc': None}
    for step in range(int(horizon/.05)+1):
        future = step*.05
        positions = {actor: point(t['route'], t['progress']+t['speed']*(now-t['time']+future))
                     for actor, t in tracks.items()}
        if overlap(positions['car'], positions['pedestrian'], margin):
            # ADR 0002: التحذير فقط مما يقع في ظل الحافلة الآن؛ الطفل الظاهر يراه السائق.
            car, pedestrian = (point(t['route'], t['progress']+t['speed']*(now-t['time']))
                               for t in (tracks['car'], tracks['pedestrian']))
            if occlusion_gate and not hidden((car[0], car[1]+DRIVER_EYE_OFFSET), pedestrian):
                return {'state': 'CLEAR_WITHIN_SCOPE', 'reason': 'السائق يراه: الطفل خارج ظل الحافلة', 'ttc': None}
            return {'state': 'HAZARD_SUSPECTED', 'reason': 'تداخل متوقع للمساحتين في الوقت نفسه', 'ttc': round(future, 2)}
    return {'state': 'CLEAR_WITHIN_SCOPE', 'reason': 'لا تداخل متوقع ضمن أفق الحساب', 'ttc': None}
