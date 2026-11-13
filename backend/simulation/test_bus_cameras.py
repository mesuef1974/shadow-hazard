from dataclasses import replace

from django.test import SimpleTestCase

from shadow_hazard.application.simulation import Scenario, run, validate
from shadow_hazard.domain.bus_cameras import CAMERAS, bearing, exit_distance, sees
from shadow_hazard.domain.motion import BUS

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
        self.assertIn('bus-cameras-v1-bus', result['version'])
        self.assertEqual({o['sensor_id'] for o in result['observations']},
                         {'bus-rear-left-camera', 'bus-front-camera'})
        for o in result['observations']:
            self.assertTrue(sees(o['actor'], o['position']))
        self.assertEqual(result['frames'][0]['in_view'], {'car': True, 'pedestrian': False})
        # الطفل غير مرئي حتى يتقدم أمام المقدمة: لا مسار له، فالحالة «غير معروف».
        self.assertEqual(result['frames'][10]['prediction']['state'], 'UNKNOWN')

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
