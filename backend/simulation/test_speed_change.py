from dataclasses import replace

from django.test import SimpleTestCase

from shadow_hazard.application.simulation import Scenario, run, validate

BASE = Scenario(key='custom', horizon=3., duration=30, reaction_time=.5, car_speed=6.,
                pedestrian_speed=2.2, driver_behavior='ignore')
# السيارة تغيب عن عدسة الحافلة بعد 17.32 م، أي عند 2.9 ث بسرعة 6 م/ث، ثم تتسارع إلى 9 م/ث.
SPEEDING = replace(BASE, car_target_speed=9., car_acceleration=1., car_acceleration_start=2.9)


def at(result, time):
    return next(f for f in result['frames'] if f['time'] == time)


class CarSpeedChangeTests(SimpleTestCase):
    def test_no_target_keeps_the_speed_constant(self):
        self.assertEqual(Scenario().car_target_speed, -1.)
        result = run(replace(BASE, duration=20))
        self.assertEqual({f['true_car_speed'] for f in result['frames'][:40]}, {6.})

    def test_speed_ramps_to_the_target_and_holds(self):
        result = run(SPEEDING)
        self.assertEqual(at(result, 2.9)['true_car_speed'], 6.)
        self.assertAlmostEqual(at(result, 3.9)['true_car_speed'], 7.)
        self.assertAlmostEqual(at(result, 5.9)['true_car_speed'], 9.)
        self.assertAlmostEqual(at(result, 7.)['true_car_speed'], 9.)
        # المسافة: 6 م/ث حتى 2.9 ث، ثم تسارع ثانية واحدة يضيف 6.5 م.
        self.assertAlmostEqual(at(result, 3.9)['truth']['car'][1], -36.+6.*2.9+6.5, places=6)

    def test_slowing_never_drops_below_the_target(self):
        result = run(replace(SPEEDING, car_target_speed=3., car_acceleration=2.))
        self.assertAlmostEqual(min(f['true_car_speed'] for f in result['frames'][:100]), 3.)
        self.assertAlmostEqual(at(result, 4.4)['true_car_speed'], 3.)

    def test_invalid_change_is_rejected(self):
        for bad in ({'car_acceleration': 0.}, {'car_target_speed': 16.},
                    {'car_acceleration_start': -1.}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(replace(SPEEDING, **bad))


class KnownLimitTests(SimpleTestCase):
    """حدّ معروف (ADR 0009): التنبؤ يفترض سرعة ثابتة. يُحدَّث هذا الاختبار حين يُعالج الحد."""

    def test_car_speeding_up_after_leaving_the_bus_camera_gets_no_warning(self):
        constant = run(replace(BASE, sensing='bus'))['metrics']
        self.assertIsNone(constant['first_collision'])
        ignored = run(replace(SPEEDING, sensing='bus'))
        self.assertIsNotNone(ignored['metrics']['first_collision'])
        self.assertIsNone(ignored['metrics']['first_prediction_alert'])
        self.assertTrue(at(ignored, 3.)['tracks']['car']['coasted'])
        responded = run(replace(SPEEDING, sensing='bus', driver_behavior='respond'))
        self.assertEqual(responded['metrics']['first_collision'],
                         ignored['metrics']['first_collision'])
