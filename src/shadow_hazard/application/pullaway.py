"""محاكاة قابلة للتكرار لتحذير سائق الحافلة عند التحرك: حقيقة ← رصد ← قرار ← سائق ← تقييم.

مستقلة عن application.simulation حتى لا تتغير أرقام بروتوكول النواة. السائق هنا نموذج بافتراضات
مكتوبة، والنتيجة لا تُعمَّم على حافلة حقيقية.
"""
import math
import random
from dataclasses import asdict, dataclass, replace

from shadow_hazard.domain.motion import BUS, ROUTES, SIZES, point
from shadow_hazard.domain.pullaway import (
    FRAME,
    VERSION,
    assess_pullaway,
    danger_zone,
    inside,
    shifted,
)

DT = .1
# افتراضات نموذج الحافلة: تسارع هادئ عند الانطلاق، وفرملة عند التحذير، وسرعة قصوى قرب المدرسة.
BUS_ACCELERATION = 1.
BUS_BRAKING = 3.
BUS_CRUISE = 3.
# مجال كاميرا المقدمة في المسقط: 3 م على كل جانب و6 م أمام المقدمة (افتراض لعدسة واسعة).
VIEW_SIDE = 3.
VIEW_DEPTH = 6.
FAULT_AT = 2.
# نافذة قصيرة لتقدير السرعة: الطفل ينعطف من الرصيف إلى أمام الحافلة، والنافذة الطويلة تؤخّر كشف ذلك.
SPEED_WINDOW = 3
# الطفل الواقف يقف أمام منتصف الحافلة: 4 م على الرصيف ثم 2.5 م في العبور.
STAND_PROGRESS = 6.5
CHILDREN = ('cross', 'sidewalk', 'stand_front', 'crouch')
DRIVERS = ('obeys', 'ignores')
FAULTS = ('', 'camera_stale', 'low_confidence')


@dataclass(frozen=True)
class PullawayScenario:
    key: str = 'front_cross'
    title: str = 'طفل يعبر أمام الحافلة لحظة انطلاقها'
    child: str = 'cross'
    child_speed: float = 1.4
    child_delay: float = 0.
    depart_at: float = 3.
    driver: str = 'obeys'
    reaction_time: float = .8
    fault: str = ''
    noise: float = 0.
    seed: int = 1
    duration: int = 20


def catalogue():
    base = PullawayScenario()
    variants = [base]
    for key, title, changes in [
        ('front_ignored', 'السائق يتجاهل التحذير', {'driver': 'ignores'}),
        ('crossed_before', 'الطفل عبر قبل الانطلاق', {'depart_at': 9.}),
        ('sidewalk', 'الطفل يكمل على الرصيف', {'child': 'sidewalk'}),
        ('stand_front', 'الطفل يقف أمام الحافلة', {'child': 'stand_front'}),
        ('crouch', 'الطفل ينحني أمام الحافلة فيُفقد رصده', {'child': 'crouch'}),
        ('run_late', 'طفل يركض أمام حافلة بدأت تتحرك', {'child_speed': 3., 'child_delay': 3.5}),
        ('noise', 'ضوضاء في تحديد الموقع', {'noise': .15}),
        ('camera_stale', 'كاميرا المقدمة تتجمد', {'fault': 'camera_stale'}),
        ('low_confidence', 'ثقة ضعيفة في الرصد', {'fault': 'low_confidence'}),
    ]:
        variants.append(replace(base, key=key, title=title, **changes))
    return variants


def validate(s):
    bounds = {'child_speed': (0, 4), 'child_delay': (0, 10), 'depart_at': (0, 15),
              'reaction_time': (0, 5), 'noise': (0, .5)}
    for name, (low, high) in bounds.items():
        value = getattr(s, name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not low <= value <= high):
            raise ValueError(f'قيمة غير صالحة: {name}')
    if type(s.seed) is not int or not 0 <= s.seed <= 1000000 or s.duration not in (20, 30):
        raise ValueError('بذرة التجربة أو مدتها غير صالحة')
    if s.child not in CHILDREN or s.driver not in DRIVERS or s.fault not in FAULTS:
        raise ValueError('اختيار سلوك أو عطل غير صالح')


def struck(bus, child):
    half = SIZES['pedestrian'][0]/2
    return (bus[0]-half <= child[0] <= bus[2]+half and bus[1]-half <= child[1] <= bus[3]+half)


def _velocity(history):
    if len(history) < 2 or history[-1][0] <= history[0][0]:
        return (0., 0.)
    (start, first), (end, last) = history[0], history[-1]
    return ((last[0]-first[0])/(end-start), (last[1]-first[1])/(end-start))


def run(s, baseline=True):
    validate(s)
    rng = random.Random(s.seed)
    route = ((8., -4.), (8., 20.)) if s.child == 'sidewalk' else ROUTES['pedestrian']
    length = sum(math.dist(a, b) for a, b in zip(route, route[1:], strict=False))
    limit = STAND_PROGRESS if s.child in ('stand_front', 'crouch') else length
    progress = offset = speed = 0.
    sensor = track = hold_since = None
    history, frames, go = [], [], False
    for index in range(int(s.duration/DT)+1):
        now = round(index*DT, 2)
        if index:
            progress = min(limit, progress+(s.child_speed if now >= s.child_delay else 0.)*DT)
            change = BUS_ACCELERATION if go else -BUS_BRAKING
            speed = min(BUS_CRUISE, max(0., speed+change*DT))
            offset += speed*DT
        child, bus = point(route, progress), shifted(BUS, offset)
        if not (s.fault == 'camera_stale' and now >= FAULT_AT):
            sensor = {'time': now, 'frame': FRAME}
        view = (bus[0]-VIEW_SIDE, bus[3], bus[2]+VIEW_SIDE, bus[3]+VIEW_DEPTH)
        lost = s.child == 'crouch' and progress >= STAND_PROGRESS
        if sensor['time'] == now and inside(view, child) and not lost:
            measured = (child[0]+rng.gauss(0, s.noise), child[1]+rng.gauss(0, s.noise))
            history = (history+[(now, measured)])[-SPEED_WINDOW:]
            track = {'position': measured, 'velocity': _velocity(history), 'time': now,
                     'confidence': .3 if s.fault == 'low_confidence' else 1.}
        wants = now >= s.depart_at
        intent = 'moving' if speed > 0 else 'departing' if wants else 'parked'
        decision = assess_pullaway(sensor, track, intent, now, bus, speed)
        # السائق الملتزم ينتظر ما دام التحذير قائمًا؛ وإن كان متحركًا يفرمل بعد زمن ردّه.
        # «غير متاح» لا يوقفه: النظام أعلن عجزه والقرار للسائق، فلا حماية تُحسب له.
        if decision['state'] != 'HOLD':
            hold_since = None
        elif hold_since is None:
            hold_since = now
        held = (s.driver == 'obeys' and hold_since is not None
                and (speed == 0 or now >= hold_since+s.reaction_time))
        go = wants and not held
        frames.append({'time': now, 'child': child, 'bus_front': bus[3], 'bus_speed': speed,
                       'intent': intent, 'decision': decision,
                       'child_in_zone': inside(danger_zone(bus), child),
                       'collision': struck(bus, child)})

    def first(predicate):
        return next((f['time'] for f in frames if predicate(f)), None)

    def count(state):
        return sum(f['decision']['state'] == state for f in frames)

    moved = first(lambda f: f['bus_speed'] > 0)
    collision = first(lambda f: f['collision'])
    metrics = {
        'first_child_near': first(lambda f: f['decision']['state'] == 'CHILD_NEAR'),
        'first_hold': first(lambda f: f['decision']['state'] == 'HOLD'),
        'first_unavailable': first(lambda f: f['decision']['state'] == 'UNAVAILABLE'),
        'bus_moved_at': moved,
        'departure_delay': None if moved is None else round(moved-s.depart_at, 2),
        'first_collision': collision,
        'hold_samples': count('HOLD'),
        'unavailable_samples': count('UNAVAILABLE'),
        # تحذير والطفل الحقيقي خارج المنطقة: يشمل التحذير الاستباقي قبل الدخول.
        'hold_outside_zone_samples': sum(
            f['decision']['state'] == 'HOLD' and not f['child_in_zone'] for f in frames),
    }
    if baseline:
        # المقارنة: المشهد نفسه وسائق لا يستجيب لأي تحذير، أي ما يقع بلا نظام.
        reference = run(replace(s, driver='ignores'), baseline=False)['metrics']['first_collision']
        metrics.update(baseline_collision=reference,
                       prevented=reference is not None and collision is None)
    return {'version': VERSION, 'scenario': asdict(s), 'bus': BUS, 'route': route,
            'units': 'metres-seconds', 'frames': frames, 'metrics': metrics,
            'model': {'bus_acceleration': BUS_ACCELERATION, 'bus_braking': BUS_BRAKING,
                      'bus_cruise': BUS_CRUISE, 'view_side': VIEW_SIDE, 'view_depth': VIEW_DEPTH,
                      'sample_seconds': DT}}


def suite():
    """سطر مقاييس لكل سيناريو في الكتالوج."""
    return [{'key': s.key, 'title': s.title, **run(s)['metrics']} for s in catalogue()]
