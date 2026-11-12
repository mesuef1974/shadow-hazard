"""تحذير سائق الحافلة عند همّه بالتحرك وطفل في منطقة الخطر أمامها؛ بلا اعتماد على عتاد.

النواة (motion.assess) تحذّر سائقًا آخر من طفل تحجبه الحافلة. هذه الوحدة تكمّلها: الطفل الذي يمر
أمام الحافلة يقع في النقطة العمياء لسائق الحافلة نفسه، فنربط موضعه بنية السائق (واقف، يهمّ
بالتحرك، يتحرك) ونرفع مستوى التحذير بحسبها. المسقط أفقي، وليس ادعاء رؤية ثلاثية الأبعاد.
"""
from .motion import BUS

VERSION = 'bus-pullaway-v1'
FRAME = 'scene-metres-v1'
# افتراض لا قياس: منطقة الخطر 3 م أمام المقدمة ونصف متر على كل جانب، تُراجع على الحافلة الحقيقية.
ZONE_DEPTH = 3.
ZONE_SIDE = .5
# الحدود نفسها المعتمدة في motion.assess.
MAX_AGE = .5
MIN_CONFIDENCE = .6
INTENTS = ('parked', 'departing', 'moving')
STEP = .05


def shifted(bus, offset):
    """مستطيل الحافلة بعد تقدّمها offset مترًا إلى الأمام (y الموجب)."""
    return (bus[0], bus[1]+offset, bus[2], bus[3]+offset)


def danger_zone(bus=BUS):
    return (bus[0]-ZONE_SIDE, bus[3], bus[2]+ZONE_SIDE, bus[3]+ZONE_DEPTH)


def inside(zone, position):
    return zone[0] <= position[0] <= zone[2] and zone[1] <= position[1] <= zone[3]


def time_to_zone(zone, position, velocity, horizon):
    """أول لحظة ضمن الأفق يدخل فيها الطفل المنطقة بسرعته الحالية؛ None إن لم يدخل."""
    for index in range(int(horizon/STEP)+1):
        future = index*STEP
        if inside(zone, (position[0]+velocity[0]*future, position[1]+velocity[1]*future)):
            return round(future, 2)
    return None


def _result(state, level, reason, warn=(), time=None):
    return {'state': state, 'level': level, 'reason': reason, 'warn': warn, 'time_to_zone': time}


def assess_pullaway(sensor, track, intent, now, bus=BUS, bus_speed=0., horizon=2.):
    """قرار تحذير سائق الحافلة.

    sensor: آخر إطار من كاميرا المقدمة {'time', 'frame'} أو None.
    track: آخر رصد للطفل {'position', 'velocity', 'time', 'confidence'} أو None إن لم يُرصد أحد.
    intent: نية سائق الحافلة من INTENTS. bus: مستطيل الحافلة الآن. bus_speed: سرعتها للأمام.
    """
    if intent not in INTENTS:
        raise ValueError('نية غير معروفة')
    if (sensor is None or sensor['time'] > now or now-sensor['time'] > MAX_AGE
            or sensor['frame'] != FRAME):
        return _result('UNAVAILABLE', None, 'كاميرا المقدمة بلا صورة حديثة معايَرة')
    if track is None:
        return _result('CLEAR', 0, 'لا طفل مرصود أمام الحافلة')
    zone = danger_zone(bus)
    if track['time'] > now or now-track['time'] > MAX_AGE:
        # الكاميرا تعمل لكن الطفل اختفى من الرصد: إن كان آخر موضع له داخل المنطقة فلم نره يخرج.
        if inside(zone, track['position']):
            return _result('HOLD', 2, 'فُقد طفل كان داخل منطقة الخطر ولم يُرصد خارجًا منها',
                           ('driver', 'child'), 0.)
        return _result('CLEAR', 0, 'آخر رصد للطفل خارج منطقة الخطر')
    if track['confidence'] < MIN_CONFIDENCE:
        return _result('UNAVAILABLE', None, 'رصد الطفل ضعيف الثقة')
    age = now-track['time']
    velocity = track['velocity']
    position = (track['position'][0]+velocity[0]*age, track['position'][1]+velocity[1]*age)
    # السرعة منسوبة إلى الحافلة: المنطقة تتحرك معها.
    entry = time_to_zone(zone, position, (velocity[0], velocity[1]-bus_speed), horizon)
    if entry is None:
        return _result('CLEAR', 0, 'الطفل خارج منطقة الخطر ولا يتجه إليها ضمن الأفق')
    if intent == 'parked':
        return _result('CHILD_NEAR', 1, 'طفل في منطقة الخطر أمام الحافلة', ('driver',), entry)
    return _result('HOLD', 2, 'طفل في منطقة الخطر والحافلة تهمّ بالتحرك أو تتحرك',
                   ('driver', 'child'), entry)
