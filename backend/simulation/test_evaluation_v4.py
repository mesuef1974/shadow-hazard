from copy import deepcopy
from dataclasses import replace
from django.test import SimpleTestCase
from shadow_hazard.application.simulation import Scenario,run,validate
from .test_evaluation import fixture
from .evaluation_v4 import manifest,audit,pair_metrics,summarize_cohort


def extended_fixture():
    r=fixture()
    r['scenario'].update(duration=30,horizon=2.)
    r['frames']=[{**deepcopy(r['frames'][0]),'time':round(i*.1,2)} for i in range(301)]
    return r


class EvaluationV4Tests(SimpleTestCase):
    def test_balanced_manifest(self):
        rows=manifest()
        self.assertEqual(len(rows),240)
        self.assertEqual(len({(r['pair'],r['arm']) for r in rows}),240)
        self.assertTrue(all(r['config']['duration']==30 for r in rows))
        for h in (2.,3.):
            self.assertEqual(sum(r['horizon']==h and r['group']=='grid' for r in rows),108)

    def test_default_and_checkpoint_prefix_are_preserved(self):
        s=Scenario(key='custom',horizon=2.,car_speed=3.,pedestrian_speed=.8,reaction_time=.5)
        old,new=run(s),run(replace(s,duration=30))
        self.assertEqual(len(old['frames']),201);self.assertEqual(len(new['frames']),301)
        for a,b in zip(old['frames'],new['frames']):
            for key in ('truth','prediction','hidden','collision','driver_warning','driver_state'):
                self.assertEqual(a[key],b[key])
        self.assertEqual(new['scenario']['duration'],30)
        with self.assertRaises(ValueError):validate(replace(s,duration=40))

    def test_later_collision_is_not_in_twenty_second_checkpoint(self):
        r=extended_fixture();r['frames'][250]['truth']['pedestrian']=[-10.,0.]
        r['frames'][250]['collision']=True;r['metrics']['first_collision']=25.
        r['frames'][80]['prediction']['state']='HAZARD_SUSPECTED'
        r['frames'][230]['prediction']['state']='HAZARD_SUSPECTED'
        self.assertIsNone(audit(r,20)['collision'])
        m=audit(r,30)
        self.assertEqual(m['collision'],25.);self.assertEqual(m['lead'],-8.)
        self.assertEqual(m['window_lead'],-23.);self.assertTrue(m['detected'])
        self.assertEqual(m['frame_count'],301)

    def test_horizon_changes_detection_window_not_first_alert(self):
        r=extended_fixture();r['frames'][250]['truth']['pedestrian']=[-10.,0.]
        r['frames'][250]['collision']=True;r['metrics']['first_collision']=25.
        r['frames'][225]['prediction']['state']='HAZARD_SUSPECTED'
        self.assertFalse(audit(r,30)['detected'])
        r['scenario']['horizon']=3.
        self.assertTrue(audit(r,30)['detected']);self.assertEqual(audit(r,30)['lead'],-22.5)

    def test_corrupt_sampling_and_mixed_groups_rejected(self):
        r=extended_fixture();r['frames'].pop()
        with self.assertRaises(ValueError):audit(r,20)
        with self.assertRaises(ValueError):summarize_cohort([{'horizon':3.,'duration':30}],2.,30)
        a=extended_fixture();b=deepcopy(a);b['scenario']['driver_behavior']='respond'
        b['scenario']['horizon']=3.
        with self.assertRaises(ValueError):pair_metrics(a,b,30)
