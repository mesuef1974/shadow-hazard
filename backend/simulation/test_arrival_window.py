from dataclasses import replace

from django.test import SimpleTestCase

from shadow_hazard.application.simulation import Scenario, run, validate
from shadow_hazard.domain.motion import ROUTES, assess, swept_overlap, travel, travel_bounds

# المشهد نفسه في test_speed_change: بسرعة ثابتة لا اصطدام، وبتسارع بعد الغياب عن العدسة اصطدام.
BASE = Scenario(key='custom', horizon=3., duration=30, reaction_time=.5, car_speed=6.,
                pedestrian_speed=2.2, driver_behavior='ignore', sensing='bus')
SPEEDING = replace(BASE, car_target_speed=9., car_acceleration=1., car_acceleration_start=2.9)


def track(actor, progress, speed, time=10.):
    return {'progress': progress, 'speed': speed, 'time': time, 'confidence': 1.,
            'frame': 'scene-metres-v1', 'route': ROUTES[actor]}


class TravelBoundsTests(SimpleTestCase):
    def test_no_envelope_means_the_constant_speed_distance(self):
        self.assertEqual(travel_bounds(6., 3., 0.), (18., 18.))
        self.assertEqual(travel_bounds(0., 3., .5), (0., 0.))

    def test_bounds_ramp_at_two_metres_per_second_squared_then_hold(self):
        # خلال الثانية الأولى ما زالت السرعة تتغير: 6 م ± نصف × 2 × 1².
        self.assertEqual(travel_bounds(6., 1., .5), (5., 7.))
        # بعد 1.5 ث تبلغ 3 أو 9 م/ث وتثبت: 6.75 + 3×1.5 و11.25 + 9×1.5.
        low, high = travel_bounds(6., 3., .5)
        self.assertAlmostEqual(low, 11.25)
        self.assertAlmostEqual(high, 24.75)

    def test_each_speed_profile_lies_between_the_bounds(self):
        self.assertEqual(travel(6., 3., 1.), 18.)
        low, high = travel_bounds(6., 3., .5)
        self.assertEqual((travel(6., 3., .5), travel(6., 3., 1.5)), (low, high))
        self.assertTrue(low < travel(6., 3., .75) < 18. < travel(6., 3., 1.25) < high)

    def test_swept_overlap_covers_everything_between_the_two_positions(self):
        self.assertTrue(swept_overlap((1.75, -10.), (1.75, 5.), (1.75, 0.)))
        self.assertFalse(swept_overlap((1.75, -10.), (1.75, -5.), (1.75, 0.)))
        self.assertFalse(swept_overlap((1.75, -10.), (1.75, 5.), (5., 0.)))


class ArrivalWindowDecisionTests(SimpleTestCase):
    def tracks(self):
        # سيارة على 24 م من الممر بسرعة 6 م/ث، وطفل في ظل الحافلة يبلغ مسارها بعد نحو ثانيتين.
        return {'car': track('car', 12., 6.), 'pedestrian': track('pedestrian', 5.5, 1.5)}

    def test_zero_envelope_is_the_old_prediction(self):
        tracks = self.tracks()
        self.assertEqual(assess(tracks, 10., 3., speed_envelope=0.), assess(tracks, 10., 3.))
        self.assertEqual(assess(tracks, 10., 3.)['state'], 'CLEAR_WITHIN_SCOPE')

    def test_window_warns_when_a_faster_car_could_arrive_in_time(self):
        result = assess(self.tracks(), 10., 3., speed_envelope=.5)
        self.assertEqual((result['state'], result['reason']),
                         ('HAZARD_SUSPECTED', 'تداخل ممكن ضمن نافذة وصول السيارة'))

    def test_window_grows_with_the_time_since_the_last_real_measurement(self):
        fresh = {'car': track('car', 15., 3.), 'pedestrian': track('pedestrian', 5.5, 1.5)}
        self.assertEqual(assess(fresh, 10., 3., speed_envelope=.5)['state'], 'CLEAR_WITHIN_SCOPE')
        # المسار نفسه مكمَّلًا منذ 4 ثوانٍ: الموضع الاسمي واحد، والنافذة أوسع.
        coasted = {**fresh, 'car': {**fresh['car'], 'measured': 6., 'coasted': True}}
        self.assertEqual(assess(coasted, 10., 3., speed_envelope=.5)['state'], 'HAZARD_SUSPECTED')

    def test_a_vanishing_window_is_the_constant_speed_prediction(self):
        for progress in (5.5, 7., 9.5):
            with self.subTest(progress=progress):
                tracks = {'car': track('car', 20., 6.),
                          'pedestrian': track('pedestrian', progress, 1.5)}
                plain, narrow = (assess(tracks, 10., 3., speed_envelope=e) for e in (0., 1e-9))
                self.assertEqual((plain['state'], plain['ttc']), (narrow['state'], narrow['ttc']))

    def test_shadow_gate_still_silences_a_child_the_driver_sees(self):
        # طفل واقف في مسار السيارة أمام الحافلة: ظاهر للسائق من كل موضع ممكن.
        tracks = {'car': track('car', 12., 6.), 'pedestrian': track('pedestrian', 9.5, 0.)}
        result = assess(tracks, 10., 3., speed_envelope=.5)
        self.assertEqual(result['reason'], 'السائق يراه: الطفل خارج ظل الحافلة')
        self.assertEqual(assess(tracks, 10., 3., occlusion_gate=False, speed_envelope=.5)['state'],
                         'HAZARD_SUSPECTED')


class ArrivalWindowRunTests(SimpleTestCase):
    def test_default_is_no_window_and_bad_values_are_rejected(self):
        self.assertEqual(Scenario().speed_envelope, 0.)
        self.assertNotIn('arrival-window', run(replace(BASE, duration=20))['version'])
        with self.assertRaises(ValueError):
            validate(replace(BASE, speed_envelope=1.))

    def test_window_recovers_the_car_that_speeds_up_out_of_view(self):
        windowed = replace(SPEEDING, speed_envelope=.5)
        ignored = run(windowed)
        self.assertIn('arrival-window-v1', ignored['version'])
        alert, collision = (ignored['metrics'][k] for k in ('first_prediction_alert',
                                                           'first_collision'))
        self.assertIsNotNone(collision)
        self.assertGreaterEqual(collision-alert, 3.)
        responded = run(replace(windowed, driver_behavior='respond'))
        self.assertIsNone(responded['metrics']['first_collision'])
        self.assertIsNotNone(responded['metrics']['resume_start'])

    def test_window_costs_a_warning_for_a_car_that_keeps_its_speed(self):
        # الثمن المعروف: المشهد نفسه بسرعة ثابتة لا اصطدام فيه، والنافذة تحذّر منه.
        self.assertIsNone(run(BASE)['metrics']['first_prediction_alert'])
        windowed = run(replace(BASE, speed_envelope=.5))['metrics']
        self.assertIsNone(windowed['first_collision'])
        self.assertIsNotNone(windowed['first_prediction_alert'])
