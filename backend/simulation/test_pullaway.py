from dataclasses import replace

from django.test import SimpleTestCase

from shadow_hazard.application.pullaway import PullawayScenario, catalogue, run, suite
from shadow_hazard.domain.motion import BUS, VERSION
from shadow_hazard.domain.pullaway import assess_pullaway, danger_zone, shifted, time_to_zone

FRESH = {'time': 10., 'frame': 'scene-metres-v1'}


def track(position, velocity=(0., 0.), time=10., confidence=1.):
    return {'position': position, 'velocity': velocity, 'time': time, 'confidence': confidence}


class PullawayDecisionTests(SimpleTestCase):
    def test_zone_sits_in_front_of_the_bus_and_moves_with_it(self):
        self.assertEqual(danger_zone(), (3.5, -2.5, 7.5, .5))
        self.assertEqual(danger_zone(shifted(BUS, 2.)), (3.5, -.5, 7.5, 2.5))

    def test_level_rises_with_driver_intent(self):
        child = track((5.5, -1.))
        parked = assess_pullaway(FRESH, child, 'parked', 10.)
        departing = assess_pullaway(FRESH, child, 'departing', 10.)
        self.assertEqual((parked['state'], parked['level'], parked['warn']),
                         ('CHILD_NEAR', 1, ('driver',)))
        self.assertEqual((departing['state'], departing['level'], departing['warn']),
                         ('HOLD', 2, ('driver', 'child')))
        self.assertEqual(assess_pullaway(FRESH, child, 'moving', 10.)['state'], 'HOLD')

    def test_child_outside_the_zone_is_clear_unless_heading_into_it(self):
        beside = track((8., 0.))
        self.assertEqual(assess_pullaway(FRESH, beside, 'departing', 10.)['state'], 'CLEAR')
        walking_in = assess_pullaway(FRESH, track((8., 0.), (-1.4, 0.)), 'departing', 10.)
        self.assertEqual((walking_in['state'], walking_in['time_to_zone']), ('HOLD', .4))
        self.assertIsNone(time_to_zone(danger_zone(), (8., 0.), (1.4, 0.), 2.))
        self.assertEqual(assess_pullaway(FRESH, None, 'departing', 10.)['state'], 'CLEAR')

    def test_moving_bus_closes_on_a_child_standing_ahead(self):
        ahead = track((5.5, 2.))
        states = [assess_pullaway(FRESH, ahead, 'moving', 10., bus_speed=speed)['state']
                  for speed in (0., 2.)]
        self.assertEqual(states, ['CLEAR', 'HOLD'])

    def test_unavailable_when_camera_is_stale_uncalibrated_or_unsure(self):
        child = track((5.5, -1.))
        late, early = {**FRESH, 'time': 9.4}, {**FRESH, 'time': 10.2}
        for sensor in (None, late, early, {**FRESH, 'frame': 'invalid'}):
            result = assess_pullaway(sensor, child, 'departing', 10.)
            self.assertEqual((result['state'], result['level'], result['warn']),
                             ('UNAVAILABLE', None, ()))
        weak = assess_pullaway(FRESH, track((5.5, -1.), confidence=.3), 'departing', 10.)
        self.assertEqual(weak['state'], 'UNAVAILABLE')

    def test_child_lost_inside_the_zone_keeps_the_hold(self):
        lost_inside = assess_pullaway(FRESH, track((5.5, -1.), time=7.), 'departing', 10.)
        lost_outside = assess_pullaway(FRESH, track((1., 0.), time=7.), 'departing', 10.)
        self.assertEqual(lost_inside['state'], 'HOLD')
        self.assertEqual(lost_outside['state'], 'CLEAR')

    def test_unknown_intent_is_rejected(self):
        with self.assertRaises(ValueError):
            assess_pullaway(FRESH, None, 'reversing', 10.)


class PullawaySimulationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rows = {row['key']: row for row in suite()}

    def test_obeying_driver_waits_and_the_baseline_collision_is_prevented(self):
        row = self.rows['front_cross']
        self.assertEqual((row['first_hold'], row['baseline_collision'], row['first_collision']),
                         (3., 5.1, None))
        self.assertTrue(row['prevented'])
        self.assertGreater(row['bus_moved_at'], 5.1)

    def test_warning_is_not_a_brake(self):
        row = self.rows['front_ignored']
        self.assertEqual((row['first_hold'], row['first_collision'], row['prevented']),
                         (3., 5.1, False))

    def test_no_hold_when_the_child_is_not_in_front(self):
        for key in ('crossed_before', 'sidewalk'):
            row = self.rows[key]
            self.assertEqual((row['hold_samples'], row['first_collision'], row['departure_delay']),
                             (0, None, .1))
        self.assertEqual(self.rows['crossed_before']['first_child_near'], 3.)

    def test_bus_never_leaves_while_a_child_stays_or_vanishes_in_front(self):
        for key in ('stand_front', 'crouch'):
            row = self.rows[key]
            self.assertIsNone(row['bus_moved_at'])
            self.assertIsNone(row['first_collision'])
            self.assertTrue(row['prevented'])

    def test_faults_say_unavailable_and_claim_no_protection(self):
        for key in ('camera_stale', 'low_confidence'):
            row = self.rows[key]
            self.assertEqual((row['hold_samples'], row['prevented']), (0, False))
            self.assertGreater(row['unavailable_samples'], 0)
            self.assertEqual(row['first_collision'], row['baseline_collision'])

    def test_known_limit_fast_child_from_beside_a_moving_bus(self):
        # حدّ معروف موثَّق في ADR 0008: الكاميرا لا ترى ما خلف مستوى المقدمة، فالتحذير يتأخر.
        row = self.rows['run_late']
        self.assertEqual((row['first_hold'], row['first_collision'], row['prevented']),
                         (4.8, 5.1, False))

    def test_runs_are_repeatable_and_validated(self):
        noisy = replace(PullawayScenario(), noise=.15, seed=7)
        self.assertEqual(run(noisy)['metrics'], run(noisy)['metrics'])
        self.assertEqual(len(catalogue()), 10)
        for bad in ({'child': 'fly'}, {'driver': 'sleeps'}, {'fault': 'x'}, {'child_speed': 9.},
                    {'seed': -1}, {'duration': 5}, {'noise': float('nan')}):
            with self.assertRaises(ValueError):
                run(replace(PullawayScenario(), **bad))

    def test_core_protocol_version_is_untouched(self):
        self.assertEqual(VERSION, 'route-occupancy-v4-analytic-motion-occlusion-gate-v1')
