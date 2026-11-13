"""مجال رؤية كاميرتي الحافلة في المسقط الأفقي (ADR 0007)؛ بلا اعتماد على أي حسّاس.

إسفين أفقي لكل كاميرا: موضع واتجاه وزاوية عدسة. لا يمثّل الارتفاع ولا الميل ولا المدى ولا
دقة الصورة، فهو حدّ هندسي لما قد تراه الكاميرا، لا ادعاء رؤية ثلاثية الأبعاد.
"""
from dataclasses import dataclass
from math import atan2, degrees, radians, tan

from shadow_hazard.domain.motion import BUS

VERSION = 'bus-cameras-v3'


@dataclass(frozen=True)
class Camera:
    sensor_id: str
    position: tuple
    heading: tuple
    fov_degrees: float


# كاميرا السيارات: أعلى الزاوية الخلفية اليسرى، إلى الخلف، عدسة 40 درجة.
# كاميرا الطفل: أعلى منتصف المقدمة، إلى الأمام، عدسة 120 درجة.
CAMERAS = {
    'car': Camera('bus-rear-left-camera', (BUS[0], BUS[1]), (0., -1.), 40.),
    'pedestrian': Camera('bus-front-camera', ((BUS[0]+BUS[2])/2, BUS[3]), (0., 1.), 120.),
}


def bearing(camera, position):
    """الزاوية بالدرجات بين محور الكاميرا والنقطة؛ None إن كانت النقطة خلف الكاميرا."""
    dx, dy = position[0]-camera.position[0], position[1]-camera.position[1]
    along = dx*camera.heading[0]+dy*camera.heading[1]
    across = dx*camera.heading[1]-dy*camera.heading[0]
    if along <= 1e-9:
        return None
    return degrees(atan2(abs(across), along))


# كاميرا المرآة (مقترحة، ADR 0009): أعلى الزاوية الأمامية اليسرى، إلى الخلف بمحاذاة جانب
# الحافلة، عدسة 90 درجة. تغطي السيارة من حيث تفقدها الكاميرا الخلفية إلى ما قبل الممر.
MIRROR = Camera('bus-front-left-mirror-camera', (BUS[0], BUS[3]), (0., -1.), 90.)
LAYOUTS = {
    'bus': {actor: (camera,) for actor, camera in CAMERAS.items()},
    'bus_mirror': {'car': (CAMERAS['car'], MIRROR), 'pedestrian': (CAMERAS['pedestrian'],)},
}


def viewer(actor, position, layout='bus'):
    """أول كاميرا في التخطيط ترى النقطة، أو None."""
    for camera in LAYOUTS[layout][actor]:
        angle = bearing(camera, position)
        if angle is not None and angle <= camera.fov_degrees/2+1e-9:
            return camera
    return None


def sees(actor, position, layout='bus'):
    return viewer(actor, position, layout) is not None


def exit_distance(actor, lateral):
    """أقصر مسافة على محور الكاميرا تبقى عندها نقطة مزاحة جانبيًا بـlateral داخل المجال."""
    return abs(lateral)/tan(radians(CAMERAS[actor].fov_degrees/2))
