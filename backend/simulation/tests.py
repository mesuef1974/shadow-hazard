from dataclasses import replace
import math
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, Client
from workspace.models import Project
from shadow_hazard.domain.motion import ROUTES, GEOMETRY, BUS, CROSSWALK_HALF_WIDTH, STOP_LINE_Y, assess, hidden, overlap, point, project
from shadow_hazard.application.simulation import Scenario, catalogue, run, observed_car_stopped
from shadow_hazard.adapters.vision import MarkerCamera
from .models import SimulationRun
from .services import checksum, save_run, suite


class MotionTests(SimpleTestCase):
    def test_responding_driver_brakes_again_for_returning_pedestrian(self):
        for mode in ('synthetic','vision'):
            result=run(Scenario(key='custom',signals='auto',pedestrian_behavior='reverse',
                driver_behavior='respond',car_speed=3,pedestrian_speed=2,behavior_time=13,horizon=2,mode=mode))
            self.assertIsNone(result['metrics']['first_collision'])
            cycles=result['response_cycles']
            self.assertGreaterEqual(len(cycles),2)
            self.assertGreater(cycles[1]['alert'],cycles[0]['resume'])
            self.assertAlmostEqual(cycles[1]['brake_start']-cycles[1]['alert'],.8)
            self.assertEqual(result['metrics']['resume_start'],cycles[0]['resume'])
            second=next(f for f in result['frames'] if f['time']==cycles[1]['alert'])
            self.assertTrue(second['hazards_active'])
            self.assertEqual(second['driver_state'],'reaction')
            self.assertTrue(any(e['event']=='بداية فرملة جديدة' for e in result['events']))

    def test_braking_distance_matches_constant_deceleration_and_collision(self):
        result=run(Scenario(key='custom',driver_behavior='brake',car_speed=11.7,horizon=2))
        self.assertAlmostEqual(result['frames'][-1]['truth']['car'][1],-36+11.7**2/4)
        self.assertIsNotNone(result['metrics']['first_collision'])

    def test_acceleration_distance_matches_constant_acceleration(self):
        result=run(Scenario(key='custom',driver_behavior='accelerate',car_speed=2))
        self.assertAlmostEqual(result['frames'][40]['truth']['car'][1],-36+2*4+.25*4**2)

    def test_catalogue_terminal_car_does_not_claim_motion(self):
        for scenario in catalogue():
            result=run(scenario)
            for scene in (result,result['comparison']):
                if scene is None: continue
                for frame in scene['frames']:
                    if frame['truth']['car']==scene['routes']['car'][-1]:
                        self.assertEqual(frame['true_car_speed'],0,scenario.key)
                        self.assertEqual(frame['driver_state'],'finished',scenario.key)

    def test_crossing_geometry_is_shared_and_stop_line_is_enforced(self):
        result=run(Scenario(key='custom',signals='auto',pedestrian_behavior='obey',horizon=2))
        self.assertEqual(result['geometry'],GEOMETRY)
        self.assertEqual(CROSSWALK_HALF_WIDTH*2,3)
        self.assertEqual(-CROSSWALK_HALF_WIDTH-STOP_LINE_Y,1)
        self.assertLessEqual(BUS[3],STOP_LINE_Y)
        # Stopping outside the stripes but beyond the stop line must not grant green.
        track={'route':ROUTES['car'],'progress':32,'speed':0.,'direct_motion':False}
        self.assertFalse(observed_car_stopped(track,.2))
        self.assertTrue(observed_car_stopped({**track,'progress':31},.2))
        for f in result['frames']:
            if f['pedestrian_signal']=='GREEN':
                car_y=f['truth']['car'][1]
                self.assertTrue(car_y-2>CROSSWALK_HALF_WIDTH+.2 or
                                (car_y+2<STOP_LINE_Y-.2 and f['true_car_speed']<.1))

    def test_car_stays_centred_inside_lane_in_both_success_views(self):
        result=run(next(s for s in catalogue() if s.key=='success'))
        for scene in (result,result['comparison']):
            for frame in scene['frames']:
                x=frame['truth']['car'][0]
                self.assertEqual(x,3.5/2)
                self.assertGreater(x-.9,0)
                self.assertLess(x+.9,3.5)
                if 'car' in frame['tracks']:
                    self.assertEqual(frame['tracks']['car']['route'],scene['routes']['car'])

    def test_recovered_request_does_not_brake_car_after_crossing(self):
        for mode in ('synthetic', 'vision'):
            for driver in ('respond', 'slow', 'premature'):
                with self.subTest(mode=mode, driver=driver):
                    result=run(Scenario(key='custom', signals='auto', pedestrian_behavior='obey',
                                        driver_behavior=driver, sensor_fault='loss', horizon=2, mode=mode))
                    self.assertTrue(result['metrics']['crossing_complete'])
                    self.assertIsNotNone(result['metrics']['first_green'])
                    for key in ('first_alert', 'brake_start', 'car_stopped', 'resume_start', 'hazards_start'):
                        self.assertIsNone(result['metrics'][key],key)
                    self.assertFalse(any(f['hazards_active'] or f['driver_warning'] for f in result['frames']))
                    self.assertEqual(result['frames'][-1]['driver_state'],'finished')

    def test_camera_quantization_explains_small_motion_delay(self):
        camera=MarkerCamera()
        positions=[camera.capture('car',(3,-9.6+.75*t*t))[0] for t in (0,.1,.2,.3)]
        self.assertEqual(positions[0],positions[1])
        self.assertGreater(positions[2][1],positions[1][1])
        self.assertGreater(positions[3][1],positions[2][1])

    def test_direct_displacement_vetoes_zero_smoothed_speed(self):
        track={'route':ROUTES['car'],'progress':20,'speed':0.,'direct_motion':False}
        self.assertTrue(observed_car_stopped(track,.2))
        self.assertFalse(observed_car_stopped({**track,'direct_motion':True},.2))

    def test_premature_motion_detection_and_signal_delays_match_frames(self):
        for mode, delay in (('synthetic',.1),('vision',.2)):
            r=run(Scenario(key='custom',signals='auto',pedestrian_behavior='obey',driver_behavior='premature',horizon=2,mode=mode))
            m=r['metrics']
            self.assertEqual(m['resume_start'],6.4)
            self.assertEqual(m['first_resumed_motion_sample'],6.5)
            self.assertEqual(m['motion_detection_delay'],delay)
            self.assertEqual(m['signal_withdrawal_delay'],delay)
            detected=next(f for f in r['frames'] if f['time']==m['motion_detected_after_resume'])
            self.assertTrue(detected['car_motion_detected'])
            self.assertEqual(detected['pedestrian_signal'],'RED')
            self.assertEqual(m['first_collision'],9.7)
            self.assertEqual(m['green_while_car_moving_samples'],0 if mode=='synthetic' else 1)

    def test_missing_observations_do_not_claim_direct_motion(self):
        r=run(Scenario(key='custom',signals='auto',pedestrian_behavior='obey',dropout=1))
        self.assertFalse(any(f['car_motion_detected'] for f in r['frames']))
        self.assertIsNone(r['metrics']['motion_detection_delay'])
        normal=run(Scenario(key='custom',signals='auto',pedestrian_behavior='obey'))
        self.assertIsNone(normal['metrics']['green_revoked_after_resume'])

    def test_unavailable_samples_remain_counted_during_driver_intervention(self):
        r=run(Scenario(key='custom',horizon=2,signals='auto',pedestrian_behavior='obey',sensor_fault='loss'))
        expected=sum(f['time']<=18 and f['prediction']['state'] in ('UNKNOWN','DEGRADED') for f in r['frames'])
        self.assertEqual(r['metrics']['unavailable'],expected)

    def test_late_reverse_starts_from_visible_endpoint(self):
        result=run(Scenario(key='custom',driver_behavior='parked',pedestrian_behavior='reverse',behavior_time=15,pedestrian_speed=4))
        self.assertEqual(result['frames'][149]['truth']['pedestrian'],(-3,0))
        self.assertGreater(result['frames'][150]['truth']['pedestrian'][0],-3)
        self.assertEqual(result['frames'][-1]['truth']['pedestrian'],(8,-4))

    def test_terminal_car_reports_zero_speed_and_finished(self):
        for driver in ('ignore','respond','slow','accelerate','premature'):
            result=run(Scenario(key='custom',driver_behavior=driver,horizon=2))
            terminal=[f for f in result['frames'] if f['truth']['car']==ROUTES['car'][-1]]
            self.assertTrue(terminal,driver)
            self.assertTrue(all(f['true_car_speed']==0 and f['driver_state']=='finished' and not f['hazards_active'] for f in terminal))

    def test_partial_braking_is_reported_without_claiming_stop(self):
        result=run(Scenario(key='custom',driver_behavior='slow',horizon=2))
        self.assertAlmostEqual(result['metrics']['brake_start'],result['metrics']['first_alert']+.8)
        self.assertIsNone(result['metrics']['car_stopped'])
        self.assertEqual(result['frames'][50]['driver_state'],'slowing')
        self.assertEqual(result['frames'][60]['driver_state'],'reduced_speed')
        self.assertEqual(result['frames'][60]['true_car_speed'],3)

    def test_prevented_collision_is_not_counted_as_false_alarm(self):
        result=run(Scenario(key='custom',horizon=2,driver_behavior='respond',reaction_time=.5))
        self.assertIsNone(result['metrics']['first_collision'])
        self.assertEqual(result['metrics']['fp'],0)
        self.assertGreater(result['metrics']['intervened'],0)
        self.assertEqual(result['metrics']['evaluation_basis'],'pre_intervention_only')
        warning=next(f for f in result['frames'] if f['prediction']['state']=='HAZARD_SUSPECTED')
        self.assertEqual(warning['evaluation'],'driver_intervention')

    def test_all_signal_direction_sensor_combinations_match_recorded_results(self):
        for side in ('right','left'):
            for signals in ('auto','off'):
                for fault in ('','loss','missing','clock','calibration'):
                    for pedestrian in ('cross','standing','stop','pause_crossing','reverse','run','away','obey'):
                        if signals=='off' and pedestrian=='obey':
                            continue
                        with self.subTest(side=side,signals=signals,fault=fault,pedestrian=pedestrian):
                            r=run(Scenario(key='custom',signals=signals,crossing_side=side,sensor_fault=fault,pedestrian_behavior=pedestrian,horizon=2))
                            frames=r['frames']
                            self.assertEqual(r['metrics']['first_collision'],next((f['time'] for f in frames if f['collision']),None))
                            self.assertEqual(r['metrics']['first_green'],next((f['time'] for f in frames if f['pedestrian_signal']=='GREEN'),None))
                            for f in frames:
                                self.assertEqual(f['collision'],overlap(f['truth']['car'],f['truth']['pedestrian']))
                                if signals=='off': self.assertEqual(f['pedestrian_signal'],'OFF')
                                if f['prediction']['state'] in ('UNKNOWN','DEGRADED') and signals=='auto':
                                    self.assertEqual(f['pedestrian_signal'],'UNAVAILABLE')

    def test_builder_obedient_pedestrian_waits_for_observed_stop(self):
        for mode in ('synthetic', 'vision'):
            for side in ('right', 'left'):
                with self.subTest(mode=mode, side=side):
                    result=run(Scenario(key='custom',horizon=2,signals='auto',pedestrian_behavior='obey',mode=mode,crossing_side=side))
                    m=result['metrics']
                    self.assertIsNone(m['first_collision'])
                    self.assertGreater(m['first_green'],m['car_stopped'])
                    self.assertTrue(m['crossing_complete'])
                    self.assertGreater(m['resume_start'],m['first_green'])
                    entry=next(e for e in result['events'] if e['event']=='بدء العبور')
                    self.assertEqual(entry['signal'],'GREEN')
                    self.assertTrue(any(f['crossing_request'] and abs(f['tracks']['pedestrian']['speed'])<.01 for f in result['frames'] if 'pedestrian' in f['tracks']))

    def test_builder_uncertain_sensors_never_grant_green(self):
        for values in ({'sensor_fault':'missing'},{'sensor_fault':'calibration'},{'sensor_fault':'clock'},{'latency':.8},{'confidence':.2},{'dropout':1}):
            with self.subTest(values=values):
                result=run(Scenario(key='custom',signals='auto',pedestrian_behavior='obey',**values))
                self.assertIsNone(result['metrics']['first_green'])
                self.assertFalse(result['metrics']['crossing_complete'])
                self.assertFalse(any(f['pedestrian_signal']=='GREEN' for f in result['frames']))

    def test_builder_independent_disobedience_and_driver_choices(self):
        base=Scenario(key='custom',horizon=2,signals='auto',pedestrian_behavior='cross',driver_behavior='ignore')
        ignored=run(base)
        self.assertIsNotNone(ignored['metrics']['first_collision'])
        self.assertIsNone(ignored['metrics']['brake_start'])
        obeyed=run(replace(base,pedestrian_behavior='obey'))
        self.assertIsNone(obeyed['metrics']['first_collision'])
        green=next(f for f in obeyed['frames'] if f['pedestrian_signal']=='GREEN')
        self.assertGreater(green['truth']['car'][1]-2,CROSSWALK_HALF_WIDTH+base.margin)
        stopped=run(replace(base,driver_behavior='respond',pedestrian_behavior='pause_crossing'))
        middle=[f for f in stopped['frames'] if f['truth']['pedestrian']==(3,0)]
        self.assertGreaterEqual(len(middle),30)
        self.assertGreater(stopped['metrics']['resume_start'],middle[-1]['time'])

    def test_builder_behavior_combinations_are_repeatable(self):
        for pedestrian in ('cross','obey','standing','stop','pause_crossing','reverse','run','away'):
            for driver in ('respond','ignore','slow','brake','accelerate','parked','premature'):
                with self.subTest(pedestrian=pedestrian,driver=driver):
                    s=Scenario(key='custom',signals='auto',pedestrian_behavior=pedestrian,driver_behavior=driver,horizon=2)
                    result=run(s)
                    self.assertEqual(checksum(result),checksum(run(s)))
                    for f in result['frames']:
                        if f['pedestrian_signal']=='GREEN':
                            self.assertNotIn(f['prediction']['state'],('UNKNOWN','DEGRADED'))
                            c=f['truth']['car']
                            self.assertTrue(c[1]-2>CROSSWALK_HALF_WIDTH+s.margin or (c[1]+2<STOP_LINE_Y-s.margin and f['true_car_speed']<.1))

    def test_success_resumes_after_crossing_and_flashes_four_way_lights(self):
        s=next(s for s in catalogue() if s.key=='success')
        self.assertEqual(s.horizon,2)
        result=run(s)
        m=result['metrics']
        self.assertEqual(m['hazards_start'],m['first_alert'])
        self.assertGreater(m['resume_start'],m['car_stopped'])
        waiting=[f for f in result['frames'] if m['car_stopped']<=f['time']<m['resume_start']]
        self.assertEqual(len({tuple(f['truth']['car']) for f in waiting}),1)
        active=[f for f in result['frames'] if m['hazards_start']<=f['time']<m['resume_start']]
        self.assertTrue(all(f['hazards_active'] for f in active))
        self.assertEqual({f['hazards_lit'] for f in active},{True,False})
        self.assertFalse(any(f['hazards_active'] for f in result['comparison']['frames']))
        resume=next(f for f in result['frames'] if f['time']==m['resume_start'])
        self.assertLess(resume['truth']['pedestrian'][0],-.3-s.margin)
        self.assertEqual(result['frames'][-1]['truth']['car'],ROUTES['car'][-1])
        self.assertIsNone(m['first_collision'])

    def test_success_is_a_paired_causal_experiment(self):
        s=next(s for s in catalogue() if s.key=='success')
        for mode in ('synthetic','vision'):
            with self.subTest(mode=mode):
                result=run(replace(s,mode=mode))
                reference=result['comparison']
                self.assertTrue(result['metrics']['success'])
                self.assertIsNone(result['metrics']['first_collision'])
                self.assertIsNotNone(reference['metrics']['first_collision'])
                self.assertAlmostEqual(result['metrics']['brake_start'],result['metrics']['first_alert']+s.reaction_time)
                self.assertEqual(result['frames'][0]['truth'],reference['frames'][0]['truth'])
                self.assertTrue(all(a['truth']['pedestrian']==b['truth']['pedestrian'] for a,b in zip(result['frames'],reference['frames'])))
                self.assertGreater(result['metrics']['intervened'],0)
                self.assertTrue(all(f['evaluation']=='driver_intervention' for f in result['frames'] if result['metrics']['brake_start']<=f['time']<=14))

    def test_success_not_assumed_when_response_late_or_sensor_missing(self):
        s=next(s for s in catalogue() if s.key=='success')
        late=run(replace(s,reaction_time=8))
        self.assertFalse(late['metrics']['success'])
        self.assertIsNotNone(late['metrics']['first_collision'])
        absent=run(replace(s,dropout=1))
        self.assertIsNone(absent['metrics']['brake_start'])
        self.assertFalse(absent['metrics']['success'])

    def test_route_turn_and_projection(self):
        route=ROUTES['pedestrian']
        self.assertEqual(point(route,4), (8,0))
        self.assertEqual(point(route,9), (3,0))
        self.assertEqual(project(route,(3,0)),9)

    def test_line_of_sight(self):
        self.assertTrue(hidden((3,-35),(8,-4)))
        self.assertFalse(hidden((3,-5),(3,0)))

    def test_body_sizes_matter(self):
        self.assertTrue(overlap((3,-2),(4,0)))
        self.assertFalse(overlap((3,-3),(4,0)))

    def test_missing_tracks_unknown(self):
        self.assertEqual(assess({},0)['state'],'UNKNOWN')

    def test_baseline_warns_before_visibility(self):
        r=run(Scenario());m=r['metrics']
        self.assertIsNotNone(m['first_collision'])
        self.assertGreater(m['lead_before_visible'],0)
        # ADR 0002: لا إخفاق في التحذير والطفل في الظل؛ بعد ظهوره يراه السائق.
        self.assertFalse(any(f.get('evaluation')=='fn' and f['hidden'] for f in r['frames']))

    def test_occlusion_gate_warns_only_inside_the_shadow(self):
        def tracks(child_progress,child_speed,car_progress):
            base=dict(time=0.,confidence=1.,frame='scene-metres-v1')
            return {'car':dict(base,route=ROUTES['car'],progress=car_progress,speed=5.),
                    'pedestrian':dict(base,route=ROUTES['pedestrian'],progress=child_progress,speed=child_speed)}
        # S2: طفل ظاهر في مسار السيارة والسيارة تقترب: لا تحذير لأن السائق يراه.
        visible=tracks(9.5,0.,26.)
        self.assertFalse(hidden((1.75,-9.),point(ROUTES['pedestrian'],9.5)))
        result=assess(visible,0.)
        self.assertEqual(result['state'],'CLEAR_WITHIN_SCOPE')
        self.assertIn('السائق يراه',result['reason'])
        # وضع إشارة المشاة المجمّد يبقى على سلوكه السابق.
        self.assertEqual(assess(visible,0.,occlusion_gate=False)['state'],'HAZARD_SUSPECTED')
        # طفل في ظل الحافلة يتجه إلى مسار السيارة: تحذير.
        shadowed=tracks(1.,2.,16.)
        self.assertTrue(hidden((1.75,-19.),point(ROUTES['pedestrian'],1.)))
        self.assertEqual(assess(shadowed,0.)['state'],'HAZARD_SUSPECTED')

    def test_crossing_at_different_times_does_not_warn(self):
        m=run(replace(Scenario(),pedestrian_delay=7))['metrics']
        self.assertIsNone(m['first_alert'])
        self.assertIsNone(m['first_collision'])

    def test_stationary_pedestrian(self):
        self.assertIsNone(run(replace(Scenario(),pedestrian_speed=0))['metrics']['first_alert'])

    def test_noisy_run_is_repeatable(self):
        s=replace(Scenario(),noise=.15,dropout=.2)
        self.assertEqual(checksum(run(s)),checksum(run(s)))
        self.assertNotEqual(checksum(run(s)),checksum(run(replace(s,seed=2))))

    def test_untrusted_sources_never_emit_clear_or_alert(self):
        for fault in ('calibration','clock','missing'):
            with self.subTest(fault=fault):
                frames=run(replace(Scenario(),fault=fault))['frames']
                self.assertTrue(all(f['prediction']['state'] in ('UNKNOWN','DEGRADED') for f in frames))

    def test_latency_is_not_treated_as_fresh(self):
        frames=run(replace(Scenario(),latency=.8))['frames']
        self.assertTrue(all(f['prediction']['state'] in ('UNKNOWN','DEGRADED') for f in frames))

    def test_sensor_loss_and_recovery(self):
        frames=run(replace(Scenario(),fault='loss'))['frames']
        self.assertEqual(frames[30]['prediction']['state'],'DEGRADED')
        self.assertNotIn(frames[100]['prediction']['state'], ('UNKNOWN','DEGRADED'))

    def test_stopping_exposes_false_alarms(self):
        m=run(replace(Scenario(),fault='stop'))['metrics']
        self.assertIsNone(m['first_collision'])
        self.assertGreater(m['fp'],0)

    def test_invalid_parameters(self):
        for value in (math.nan,math.inf,-1,100,True,'6'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run(replace(Scenario(),car_speed=value))

    def test_catalogue_and_grid(self):
        self.assertEqual(len(catalogue()),21)
        self.assertEqual(len(suite(grid=True)),48)
        self.assertEqual(len({s.key for s in suite(grid=True)}),48)

    def test_vision_reads_pixels_and_calibrates(self):
        camera=MarkerCamera()
        for actor in ('car','pedestrian'):
            pos,png=camera.capture(actor,(3,-10),preview=True)
            self.assertAlmostEqual(pos[0],3)
            self.assertAlmostEqual(pos[1],-10)
            self.assertTrue(png)
            self.assertIsNone(camera.capture(actor,(3,-10),visible=False)[0])

    def test_vision_pipeline_warns(self):
        result=run(replace(Scenario(),mode='vision'))
        self.assertGreater(result['metrics']['lead_before_visible'],0)
        self.assertEqual(result['observations'][0]['algorithm'],'hsv-marker-v2-40px')

    def test_utc_metadata_and_source_identity(self):
        rows=run(Scenario())['observations']
        self.assertTrue(rows[0]['captured_utc'].endswith('+00:00'))
        self.assertEqual({o['sensor_id'] for o in rows},{'left-camera','right-camera'})

    def test_tail_is_censored_not_scored_safe(self):
        frames=run(Scenario())['frames']
        self.assertEqual(frames[-1]['evaluation'],'censored')


class LabTests(TestCase):
    def test_history_omits_result_payload_but_detail_checks_it(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_login(self.supervisor)
        row=save_run(Scenario(),self.supervisor)
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get('/simulation/')
        self.assertContains(response,f'data-load="{row.pk}"')
        selected=[q['sql'] for q in queries if 'FROM "simulation_simulationrun"' in q['sql'] and 'COUNT(' not in q['sql']]
        # استعلام واحد، ولا عمود بعد result: قراءته تمرّ SQLite بصفحات النتيجة كلها.
        self.assertEqual(len(selected),1)
        for column in ('"result"','"digest"','"created_by_id"'):
            self.assertNotIn(column,selected[0].split(' FROM ')[0])
        row.refresh_from_db()
        self.assertEqual(self.client.get(f'/simulation/runs/{row.pk}/').json()['result'],row.result)
        SimulationRun.objects.filter(pk=row.pk).update(result={'tampered':True})
        self.assertEqual(self.client.get(f'/simulation/runs/{row.pk}/').status_code,409)

    def test_builder_save_replay_and_enum_validation(self):
        self.client.force_login(self.supervisor)
        payload={'key':'custom','pedestrian_behavior':'obey','driver_behavior':'respond','signals':'auto','crossing_side':'left','sensor_fault':'','horizon':2}
        response=self.client.post('/simulation/run/',payload,content_type='application/json')
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data,self.client.get(f"/simulation/runs/{data['id']}/").json())
        self.assertEqual(data['result']['scenario']['crossing_side'],'left')
        for field in ('pedestrian_behavior','driver_behavior','signals','sensor_fault','crossing_side'):
            self.assertEqual(self.client.post('/simulation/preview/',{**payload,field:'invalid'},content_type='application/json').status_code,400)
        self.assertEqual(self.client.post('/simulation/preview/',{**payload,'signals':'off'},content_type='application/json').status_code,400)

    def setUp(self):
        self.project=Project.objects.create(name='test')
        self.supervisor=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.student=get_user_model().objects.create_user(username='saud',role='student')

    def test_login_required(self):
        response = self.client.get('/simulation/')
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith('/login/'))

    def test_student_reads_but_cannot_run(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/simulation/').status_code,200)
        self.assertEqual(self.client.post('/simulation/run/',{'key':'crossing'},content_type='application/json').status_code,403)

    def test_save_and_replay(self):
        self.client.force_login(self.supervisor)
        response=self.client.post('/simulation/run/',{'key':'crossing'},content_type='application/json')
        self.assertEqual(response.status_code,200)
        data=response.json()
        replay=self.client.get(f"/simulation/runs/{data['id']}/").json()
        self.assertEqual(replay,data)
        self.assertEqual(SimulationRun.objects.count(),1)

    def test_each_scenario_preview_matches_selection_without_saving(self):
        self.client.force_login(self.supervisor)
        for scenario in catalogue():
            with self.subTest(scenario=scenario.key):
                response=self.client.post('/simulation/preview/',{'key':scenario.key},content_type='application/json')
                self.assertEqual(response.status_code,200)
                data=response.json()
                self.assertIsNone(data['id'])
                self.assertEqual(data['result']['scenario']['key'],scenario.key)
                self.assertEqual(data['result']['scenario']['fault'],scenario.fault)
                self.assertEqual(data['result']['scenario']['car_speed'],scenario.car_speed)
                self.assertEqual(data['digest'],checksum(data['result']))
        self.assertEqual(SimulationRun.objects.count(),0)

    def test_preview_retains_auth_and_validation(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.post('/simulation/preview/',{'key':'crossing'},content_type='application/json').status_code,403)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.post('/simulation/preview/',{'key':'unknown'},content_type='application/json').status_code,400)

    def test_named_motion_scenarios_behave_differently(self):
        scenarios={s.key:s for s in catalogue()}
        outputs={key:run(scenarios[key]) for key in ('crossing','late','early','standing','parked','stop','reverse','away','brake')}
        self.assertIsNotNone(outputs['crossing']['metrics']['first_collision'])
        for key in ('late','early','standing','parked','stop','reverse','away','brake'):
            self.assertIsNone(outputs[key]['metrics']['first_collision'],key)
        self.assertEqual({tuple(f['truth']['car']) for f in outputs['parked']['frames']},{ROUTES['car'][0]})
        self.assertEqual({tuple(f['truth']['pedestrian']) for f in outputs['standing']['frames']},{(8,-4)})
        self.assertEqual(outputs['reverse']['frames'][-1]['truth']['pedestrian'],(8,-4))
        self.assertTrue(all(f['truth']['pedestrian'][0]==8 for f in outputs['away']['frames']))
        self.assertEqual(len({tuple(f['truth']['pedestrian']) for f in outputs['stop']['frames'][20:]}),1)

    def test_invalid_payload_does_not_persist(self):
        self.client.force_login(self.supervisor)
        for payload in ({'key':'bad'},{'key':'crossing','car_speed':-1},[],{'key':'crossing','fault':'loss'}):
            self.assertEqual(self.client.post('/simulation/run/',payload,content_type='application/json').status_code,400)
        self.assertFalse(SimulationRun.objects.exists())

    def test_tamper_detected(self):
        row=save_run(Scenario(),self.supervisor)
        SimulationRun.objects.filter(pk=row.pk).update(result={})
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(f'/simulation/runs/{row.pk}/').status_code,409)

    def test_csrf_protected(self):
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.supervisor)
        self.assertEqual(client.post('/simulation/run/',{'key':'crossing'},content_type='application/json').status_code,403)
