from django.test import SimpleTestCase
from shadow_hazard.adapters.vision import MarkerCamera
from shadow_hazard.application.simulation import Scenario, run


class VisionResolutionTests(SimpleTestCase):
    def test_subdecimetre_motion_does_not_jump_to_one_metre_per_second(self):
        camera = MarkerCamera()
        start, _ = camera.capture('pedestrian', (5.36, 0.))
        end, _ = camera.capture('pedestrian', (5.04, 0.))
        self.assertAlmostEqual((start[0]-end[0])/.4, .8, delta=.0625)
        self.assertAlmostEqual(end[0], 5.04, delta=.0125)

    def test_response_does_not_create_old_vision_collision(self):
        result = run(Scenario(key='custom', mode='vision', car_speed=3.,
                     pedestrian_speed=.8, pedestrian_delay=2., horizon=2., reaction_time=.5))
        self.assertIsNone(result['metrics']['first_collision'])
