from copy import deepcopy
from django.test import SimpleTestCase
from .evaluation import manifest, contact, occluded, audit, pair_metrics, summarize


def fixture():
    frames = [dict(time=round(i*.1, 2), truth={'car': [-10., 0.], 'pedestrian': [8., 0.]},
                   collision=False, hidden=False, prediction={'state': 'CLEAR_WITHIN_SCOPE'}) for i in range(201)]
    return dict(frames=frames, geometry={'sizes': {'car': [1.8,4.], 'pedestrian': [.6,.6]}},
        routes={'car': [[-10.,0.]], 'pedestrian': [[8.,0.]]},
        scenario={'driver_behavior':'ignore','title':'A'}, metrics={'first_collision':None})


class EvaluationTests(SimpleTestCase):
    def test_manifest_is_balanced_and_fixed(self):
        rows=manifest()
        self.assertEqual(len(rows),120)
        self.assertEqual(len({(r['pair'],r['arm']) for r in rows}),120)
        self.assertEqual(sum(r['group']=='control' for r in rows),12)
        self.assertTrue(all(r['config']['horizon']==2 and r['config']['key']=='custom' and
                            r['config']['signals']=='off' for r in rows))

    def test_rectangle_edges_and_separation(self):
        self.assertTrue(contact((0,0),(1.2,2.3)))
        self.assertFalse(contact((0,0),(1.20001,0)))
        self.assertFalse(contact((0,0),(0,2.30001)))
        self.assertTrue(occluded((1.75,-20),(8,-4)))
        self.assertFalse(occluded((-10,0),(8,0)))

    def test_audit_detects_corruption_and_incomplete_end(self):
        r=fixture();self.assertTrue(audit(r)['complete'])
        r['geometry']['sizes']={'car':(1.8,4.),'pedestrian':(.6,.6)}
        self.assertTrue(audit(r)['complete'])
        r['routes']['car'].append([0,0]);self.assertFalse(audit(r)['complete'])
        r['frames'][10]['collision']=True
        with self.assertRaises(ValueError):audit(r)
        r=fixture();r['frames'].pop()
        with self.assertRaises(ValueError):audit(r)

    def test_event_detection_censoring_and_unavailable(self):
        r=fixture()
        r['frames'][100]['truth']['pedestrian']=[-10.,0.]
        r['frames'][100]['collision']=True;r['metrics']['first_collision']=10.
        r['frames'][80]['prediction']['state']='HAZARD_SUSPECTED'
        r['frames'][79]['prediction']['state']='UNKNOWN'
        m=audit(r);self.assertTrue(m['detected']);self.assertEqual(m['unavailable'],1)
        r['frames'][80]['prediction']['state']='UNKNOWN'
        r['frames'][79]['prediction']['state']='HAZARD_SUSPECTED'
        self.assertFalse(audit(r)['detected'])
        r=fixture();r['frames'][190]['prediction']['state']='HAZARD_SUSPECTED'
        self.assertFalse(audit(r)['false_alarm']);self.assertEqual(audit(r)['late_alerts'],1)

    def test_pair_rejects_mismatch(self):
        a=fixture();b=deepcopy(a);b['scenario']['driver_behavior']='respond'
        self.assertFalse(pair_metrics(a,b)['A']['false_alarm'])
        b['frames'][0]['truth']['pedestrian']=[9.,0.]
        with self.assertRaises(ValueError):pair_metrics(a,b)

    def test_undefined_denominators_and_unfinished_are_not_success(self):
        a=audit(fixture());b=deepcopy(a)
        positive={'pair':'one','mode':'synthetic','group':'grid','A':a,'B':b}
        self.assertEqual(summarize([positive])['verdict'],'INVALID')
        self.assertEqual(summarize([positive],expected_n=1)['verdict'],'INCONCLUSIVE')
        a.update(collision=10.,detected=True,hidden_initially=True,lead=.3)
        b['complete']=False
        negative={'pair':'two','mode':'synthetic','group':'grid','A':audit(fixture()),'B':audit(fixture())}
        r=summarize([positive,negative],expected_n=2)
        self.assertEqual(r['complete_avoidance'],0);self.assertEqual(r['verdict'],'FAIL')
        b['complete']=True
        self.assertEqual(summarize([positive,negative],expected_n=2)['verdict'],'PASS')
        self.assertEqual(summarize([positive,positive],expected_n=2)['verdict'],'INVALID')
        negative['mode']='vision'
        self.assertEqual(summarize([positive,negative],expected_n=2)['verdict'],'INVALID')
