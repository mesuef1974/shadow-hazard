from dataclasses import replace

from django.test import SimpleTestCase

from shadow_hazard.application.simulation import Scenario, run, validate
from shadow_hazard.domain.bus_cameras import CAMERAS, bearing, exit_distance, sees
from shadow_hazard.domain.motion import BUS, ROUTES, assess

# خلية خطرة من شبكة بروتوكول 731: سيارة بطيئة وطفل بطيء، يعتمد تحذيرها على إكمال المسار.
SLOW = Scenario(key='custom', horizon=3., duration=30, reaction_time=.5, car_speed=3.,
                pedestrian_speed=.8, driver_behavior='ignore')


def at(result, time):
    return next(f for f in result['frames'] if f['time'] == time)


class BusCameraGeometryTests(SimpleTestCase):
    def test_cameras_sit_on_the_bus_as_decided(self):
        self.assertEqual(CAMERAS['car'].position, (BUS[0], BUS[1]))
        self.assertEqual(CAMERAS['pedestrian'].position, ((BUS[0]+BUS[2])/2, BUS[3]))
        self.assertEqual((CAMERAS['car'].fov_degrees, CAMERAS['pedestrian'].fov_degrees),
                         (40., 120.))

    def test_car_camera_loses_the_car_before_it_reaches_the_bus(self):
        # السيارة على x=1.75، أي 2.25 م يسار الكاميرا: تخرج من عدسة 40 درجة على 6.18 م خلفها.
        self.assertAlmostEqual(exit_distance('car', BUS[0]-1.75), 6.18, places=2)
        self.assertTrue(sees('car', (1.75, -36.)))
        self.assertTrue(sees('car', (1.75, -18.7)))
        self.assertFalse(sees('car', (1.75, -18.6)))
        self.assertFalse(sees('car', (1.75, 0.)))

    def test_child_camera_sees_the_crossing_but_not_the_side_of_the_bus(self):
        self.assertFalse(sees('pedestrian', (8., -4.)))
        self.assertIsNone(bearing(CAMERAS['pedestrian'], (8., -4.)))
        self.assertTrue(sees('pedestrian', (8., 0.)))
        self.assertTrue(sees('pedestrian', (5.5, 0.)))
        # على ممر يبعد 2.5 م عن المقدمة: نصف العرض المرئي 4.33 م، فيُفقد الطفل يسار x=1.17.
        self.assertTrue(sees('pedestrian', (1.2, 0.)))
        self.assertFalse(sees('pedestrian', (1.1, 0.)))


class BusSensingRunTests(SimpleTestCase):
    def test_overhead_stays_the_default_and_unknown_sensing_is_rejected(self):
        self.assertEqual(Scenario().sensing, 'overhead')
        result = run(replace(SLOW, duration=20))
        self.assertNotIn('in_view', result['frames'][0])
        self.assertNotIn('bus-cameras', result['version'])
        self.assertEqual({o['sensor_id'] for o in result['observations']},
                         {'left-camera', 'right-camera'})
        with self.assertRaises(ValueError):
            validate(replace(SLOW, sensing='drone'))

    def test_bus_cameras_observe_only_inside_their_fields(self):
        result = run(replace(SLOW, sensing='bus'))
        self.assertIn('bus-cameras-v3-bus', result['version'])
        self.assertEqual({o['sensor_id'] for o in result['observations']},
                         {'bus-rear-left-camera', 'bus-front-camera'})
        for o in result['observations']:
            self.assertTrue(sees(o['actor'], o['position']))
        self.assertEqual(result['frames'][0]['in_view'], {'car': True, 'pedestrian': False})
        # الطفل بجانب الحافلة خارج مجال كاميرا المقدمة وهي سليمة: حالة سليمة لا عطل.
        early = result['frames'][10]['prediction']
        self.assertEqual((early['state'], early['reason']),
                         ('NOTHING_IN_VIEW', 'لا طفل في مجال الكاميرا'))
        self.assertEqual(result['frames'][10]['evaluation'], 'tn')

    def test_coasting_keeps_the_warning_after_the_car_leaves_the_field(self):
        overhead = run(SLOW)['metrics']
        bus = run(replace(SLOW, sensing='bus'))
        alert = bus['metrics']['first_prediction_alert']
        self.assertEqual(alert, overhead['first_prediction_alert'])
        self.assertLess(alert, bus['metrics']['first_collision'])
        frame = at(bus, alert)
        self.assertFalse(frame['in_view']['car'])
        self.assertTrue(frame['tracks']['car']['coasted'])
        self.assertTrue(frame['hidden'])
        # والسائق المستجيب يتجنب الاصطدام ويكمل طريقه.
        responded = run(replace(SLOW, sensing='bus', driver_behavior='respond'))
        self.assertIsNone(responded['metrics']['first_collision'])
        self.assertIsNotNone(responded['metrics']['resume_start'])

    def test_without_coasting_the_same_scene_gets_no_warning(self):
        ignored = run(replace(SLOW, sensing='bus_no_coast'))
        self.assertIsNone(ignored['metrics']['first_prediction_alert'])
        self.assertFalse(any(t.get('coasted') for f in ignored['frames']
                             for t in f['tracks'].values()))
        responded = run(replace(SLOW, sensing='bus_no_coast', driver_behavior='respond'))
        self.assertEqual(responded['metrics']['first_collision'],
                         ignored['metrics']['first_collision'])

    def test_loss_inside_the_field_degrades_instead_of_coasting(self):
        # طفل سريع يدخل المجال قبل انقطاع كاميرته (من الثانية 1 إلى 8) وهو ما زال داخله.
        lost = run(replace(SLOW, sensing='bus', pedestrian_speed=4., sensor_fault='loss'))
        frame = at(lost, 2.)
        self.assertTrue(frame['in_view']['pedestrian'])
        self.assertEqual(frame['prediction']['state'], 'DEGRADED')
        self.assertNotIn('coasted', frame['tracks']['pedestrian'])


def car_track(time=10.):
    return {'progress': 5., 'speed': 3., 'time': time, 'confidence': 1.,
            'frame': 'scene-metres-v1', 'route': ROUTES['car']}


class NothingInViewTests(SimpleTestCase):
    def test_healthy_camera_without_a_target_is_not_a_fault(self):
        both = {'car': 10., 'pedestrian': 10.}
        result = assess({'car': car_track()}, 10., sensors=both)
        self.assertEqual((result['state'], result['reason']),
                         ('NOTHING_IN_VIEW', 'لا طفل في مجال الكاميرا'))
        self.assertEqual(assess({}, 10., sensors=both)['reason'],
                         'لا سيارة ولا طفل في مجال الكاميرتين')

    def test_silent_or_late_camera_stays_unknown(self):
        tracks = {'car': car_track()}
        self.assertEqual(assess(tracks, 10.)['state'], 'UNKNOWN')
        self.assertEqual(assess(tracks, 10., sensors={'car': 10.})['state'], 'UNKNOWN')
        for stamp in (9.4, 10.1):
            with self.subTest(stamp=stamp):
                self.assertEqual(assess(tracks, 10., sensors={'pedestrian': stamp})['state'],
                                 'UNKNOWN')

    def test_stale_track_still_degrades_when_the_other_target_is_absent(self):
        result = assess({'car': car_track(time=9.)}, 10., sensors={'pedestrian': 10.})
        self.assertEqual(result['state'], 'DEGRADED')

    def test_real_camera_faults_are_still_declared_in_the_run(self):
        # كاميرا الطفل غائبة من البداية، أو مقطوعة والطفل لم يدخل مجالها بعد، أو معايرتها باطلة.
        for fault, time in (('missing', 1.), ('loss', 3.), ('calibration', 1.), ('clock', 1.)):
            with self.subTest(fault=fault):
                result = run(replace(SLOW, sensing='bus', sensor_fault=fault))
                frame = at(result, time)
                self.assertFalse(frame['in_view']['pedestrian'])
                self.assertEqual(frame['prediction']['state'], 'UNKNOWN')
        for changes in ({'latency': .8}, {'confidence': .3}, {'dropout': 1.}):
            with self.subTest(changes=changes):
                result = run(replace(SLOW, sensing='bus', **changes))
                self.assertEqual(at(result, 1.)['prediction']['state'], 'UNKNOWN')

    def test_unavailable_frames_drop_to_first_sightings(self):
        # يبقى «غير معروف» إطاران فقط: أول رصد للسيارة وأول رصد للطفل، قبل اكتمال مساريهما.
        result = run(replace(SLOW, sensing='bus'))
        unknown = [f['time'] for f in result['frames']
                   if f['prediction']['state'] in ('UNKNOWN', 'DEGRADED')]
        self.assertEqual(unknown, [0., 3.7])
        self.assertFalse(at(result, 3.6)['in_view']['pedestrian'])
        self.assertTrue(at(result, 3.7)['in_view']['pedestrian'])
        self.assertEqual(result['metrics']['first_prediction_alert'],
                         run(SLOW)['metrics']['first_prediction_alert'])

    def test_overhead_mode_never_reports_nothing_in_view(self):
        result = run(replace(SLOW, sensor_fault='missing', duration=20))
        self.assertEqual({f['prediction']['state'] for f in result['frames']}, {'UNKNOWN'})


class MirrorCameraTests(SimpleTestCase):
    """كاميرا ثالثة مقترحة للسيارة (ADR 0009): تسدّ غيابها عن العدسة الخلفية قبل الممر."""

    def test_mirror_camera_covers_the_car_from_the_rear_exit_to_the_crossing(self):
        from shadow_hazard.domain.bus_cameras import MIRROR, viewer
        self.assertEqual((MIRROR.position, MIRROR.heading, MIRROR.fov_degrees),
                         ((BUS[0], BUS[3]), (0., -1.), 90.))
        # لا فجوة: كل موضع للسيارة من بداية الطريق إلى 4.8 م قبل الممر تراه كاميرا.
        for tenth in range(-360, -48):
            self.assertTrue(sees('car', (1.75, tenth/10), 'bus_mirror'), tenth/10)
        self.assertFalse(sees('car', (1.75, -4.7), 'bus_mirror'))
        self.assertFalse(sees('car', (1.75, -18.6)))
        self.assertEqual(viewer('car', (1.75, -30.), 'bus_mirror').sensor_id,
                         'bus-rear-left-camera')
        self.assertEqual(viewer('car', (1.75, -10.), 'bus_mirror').sensor_id,
                         'bus-front-left-mirror-camera')
        # كاميرا الطفل لا تتغير.
        self.assertFalse(sees('pedestrian', (8., -4.), 'bus_mirror'))

    def test_run_tracks_the_car_with_real_observations_until_just_before_the_crossing(self):
        result = run(replace(SLOW, sensing='bus_mirror'))
        self.assertIn('bus-cameras-v3-bus_mirror', result['version'])
        self.assertEqual({o['sensor_id'] for o in result['observations'] if o['actor'] == 'car'},
                         {'bus-rear-left-camera', 'bus-front-left-mirror-camera'})
        alert = at(result, result['metrics']['first_prediction_alert'])
        self.assertTrue(alert['in_view']['car'])
        self.assertNotIn('coasted', alert['tracks']['car'])
        # الإكمال باقٍ للأمتار الأخيرة قبل الممر.
        self.assertTrue(any(f['tracks'].get('car', {}).get('coasted') for f in result['frames']))

    def test_mirror_recovers_a_car_that_changes_speed_out_of_the_rear_field(self):
        # المشهد الخطر الذي يفوت كاميرتي الحافلة: سيارة 6 م/ث تنزل إلى 3 بعد غيابها عن الخلفية.
        scene = replace(SLOW, car_speed=6., pedestrian_speed=1.5, pedestrian_delay=2.,
                        car_target_speed=3., car_acceleration=1., car_acceleration_start=2.9)
        missed = run(replace(scene, sensing='bus'))['metrics']
        self.assertIsNotNone(missed['first_collision'])
        self.assertIsNone(missed['first_prediction_alert'])
        seen = run(replace(scene, sensing='bus_mirror'))['metrics']
        self.assertLess(seen['first_prediction_alert'], seen['first_collision'])
        responded = run(replace(scene, sensing='bus_mirror', driver_behavior='respond'))
        self.assertIsNone(responded['metrics']['first_collision'])
