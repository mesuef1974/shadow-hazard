from contextlib import closing
import hashlib
import sqlite3
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.template.loader import render_to_string
from workspace.models import Project, SessionCharter, ModelPolicy
from .models import ContextSource, ContextObservation, TokenSample, CapacityEvent, CapacityHandover, TrackedSession
from .runtime_context import SCHEMA, TARGET, parse, _import, snapshot, synchronize_context


class RuntimeContextTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'source.sqlite'
        self.now = timezone.now()
        self.thread = str(uuid.uuid4())
        self.turn = str(uuid.uuid4())
        self.process = 'pid:12:'+str(uuid.uuid4())
        self.project = Project.objects.create(name='synthetic')
        self.policy = ModelPolicy.objects.create(category='synthetic')
        self.charter = SessionCharter.objects.create(key='synthetic',name='جلسة اختبار',project=self.project,policy=self.policy,thread_id=self.thread)
        self.tracked = TrackedSession.objects.create(project=self.project,external_id=self.thread,name='test')
        with closing(sqlite3.connect(self.path, isolation_level=None)) as conn:
            conn.execute('CREATE TABLE logs ('+','.join(name+' '+typ+(' PRIMARY KEY' if name=='id' else '') for name,typ in SCHEMA)+')')
        self.override = override_settings(CONTEXT_USAGE_SOURCE=self.path,LOCAL_DIR=Path(self.temp.name))
        self.override.enable(); self.addCleanup(self.override.disable)

    def row(self, pk=1, used=800, limit=1000, model='test-model', ago=0):
        stamp=self.now-timedelta(seconds=ago)
        body=(f'thread_id={self.thread} turn.id={self.turn} model={model}: post sampling token usage '
              f'turn_id={self.turn} total_usage_tokens={used} auto_compact_scope_tokens={used} '
              f'auto_compact_scope_limit=Some(900) auto_compact_limit_scope=Total auto_compact_window_prefill_tokens=None '
              f'full_context_window_limit=Some({limit}) full_context_window_limit_reached={str(used>=limit).lower()} '
              'token_limit_reached=false model_needs_follow_up=false has_pending_input=false needs_follow_up=false')
        return dict(id=pk,ts=int(stamp.timestamp()),ts_nanos=stamp.microsecond*1000,level='TRACE',target=TARGET,
            feedback_log_body=body,module_path=TARGET,file='turn.rs',line=645,thread_id=self.thread,process_uuid=self.process,estimated_bytes=100)

    def insert(self,row):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as conn:
            conn.execute('INSERT INTO logs VALUES ('+','.join('?' for _ in SCHEMA)+')',[row[name] for name,_ in SCHEMA])

    def test_full_window_denominator_and_no_billing_or_actions(self):
        billing=TokenSample.objects.create(session=self.tracked,source_id=10,sampled_at=self.now,
            total=420,input=400,cached=100,output=20,reasoning=10,context=300,context_limit=1000)
        billing_before=TokenSample.objects.values().get(pk=billing.pk)
        self.insert(self.row());before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(_import(self.path,self.now)['added'],1)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        result=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertEqual(result['percent'],80)
        self.assertEqual(result['latest'].limit,1000)
        self.assertEqual((TokenSample.objects.count(),CapacityEvent.objects.count(),CapacityHandover.objects.count()),(1,0,0))
        self.assertEqual(TokenSample.objects.values().get(pk=billing.pk),billing_before)
        from .services import summarize
        self.assertEqual(summarize(self.project)[1]['total'],420)
        self.assertFalse(any('body' in f.name for f in ContextObservation._meta.fields))

    def test_idempotent_import_and_process_restart(self):
        self.insert(self.row());_import(self.path,self.now)
        self.assertEqual(_import(self.path,self.now)['added'],0)
        self.process='pid:13:'+str(uuid.uuid4());self.insert(self.row(pk=2))
        self.assertEqual(_import(self.path,self.now)['added'],1)
        self.assertEqual(ContextObservation.objects.count(),2)

    def test_id_regression_rotates_generation_and_preserves_history(self):
        self.insert(self.row(pk=10));_import(self.path,self.now)
        generation=ContextSource.objects.get().generation
        with closing(sqlite3.connect(self.path, isolation_level=None)) as conn:conn.execute('DELETE FROM logs')
        self.insert(self.row(pk=1,used=100));_import(self.path,self.now)
        self.assertNotEqual(ContextSource.objects.get().generation,generation)
        self.assertEqual(ContextObservation.objects.count(),2)
        self.assertEqual(snapshot(self.project,now=self.now)['sessions'][0]['latest'].current,100)
        self.assertEqual(len(snapshot(self.project,now=self.now)['sessions'][0]['points']),2)

    def test_same_ids_replaced_source_rotates_generation(self):
        self.insert(self.row());_import(self.path,self.now);old=ContextSource.objects.get().generation
        with closing(sqlite3.connect(self.path, isolation_level=None)) as conn:conn.execute('UPDATE logs SET ts=ts+1')
        _import(self.path,self.now+timedelta(seconds=2))
        self.assertNotEqual(ContextSource.objects.get().generation,old)

    def test_binding_replacement_never_inherits_old_measurement(self):
        self.insert(self.row());_import(self.path,self.now)
        self.charter.thread_id=str(uuid.uuid4());self.charter.save()
        result=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertIsNone(result['latest']);self.assertEqual(result['points'],[])

    def test_conflicting_binding_not_imported(self):
        SessionCharter.objects.create(key='duplicate',name='duplicate',project=self.project,policy=self.policy,thread_id=self.thread)
        self.insert(self.row());self.assertEqual(_import(self.path,self.now)['added'],0)

    def test_stale_uses_sample_time_and_source_health(self):
        self.insert(self.row(ago=121));_import(self.path,self.now)
        row=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertFalse(row['fresh']);self.assertIsNone(row['percent']);self.assertEqual(row['age'],121)
        self.assertTrue(row['points'])

    def test_missing_source_keeps_history_but_hides_current(self):
        self.insert(self.row());_import(self.path,self.now);self.path.unlink()
        self.assertEqual(synchronize_context()['status'],'unavailable')
        row=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertFalse(row['fresh']);self.assertTrue(row['points'])

    def test_schema_change_fails_closed(self):
        self.insert(self.row());_import(self.path,self.now)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as conn:conn.execute('ALTER TABLE logs ADD COLUMN new_field TEXT')
        self.assertEqual(synchronize_context()['status'],'schema_changed')
        self.assertFalse(snapshot(self.project,now=self.now)['sessions'][0]['fresh'])

    def test_parser_rejects_ambiguous_or_unapproved_events(self):
        row=self.row()
        bodies=[row['feedback_log_body']+' total_usage_tokens=1',
            row['feedback_log_body'].replace('turn.id='+self.turn,'turn.id='+str(uuid.uuid4())),
            row['feedback_log_body'].replace('auto_compact_scope_tokens=800','auto_compact_scope_tokens=801'),
            row['feedback_log_body'].replace('full_context_window_limit_reached=false','full_context_window_limit_reached=true'),
            row['feedback_log_body'].replace('auto_compact_limit_scope=Total','auto_compact_limit_scope=BodyAfterPrefix'),
            row['feedback_log_body'].replace('full_context_window_limit=Some(1000)','full_context_window_limit=None'),
            row['feedback_log_body'].replace('has_pending_input=false','has_pending_input=unknown')]
        for body in bodies:
            with self.subTest(body_digest=hashlib.sha256(body.encode()).hexdigest()):
                with self.assertRaises(ValueError):parse({**row,'feedback_log_body':body},self.now)
        for changes in [{'level':'INFO'},{'module_path':'other'},{'line':646},{'ts_nanos':10**9},{'ts':row['ts']+20}]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):parse({**row,**changes},self.now)

    def test_unknown_model_not_guessed_and_over_capacity_not_clamped(self):
        row=self.row(used=1100)
        row['feedback_log_body']='model=other '+row['feedback_log_body']
        parsed=parse(row,self.now);self.assertEqual(parsed['model'],'');self.assertEqual(parsed['current'],1100)

    def test_rejected_latest_event_stays_unavailable_until_valid_new_event(self):
        self.insert(self.row());_import(self.path,self.now)
        row=self.row(pk=2);row['level']='INFO';self.insert(row)
        self.assertEqual(_import(self.path,self.now)['status'],'invalid_data')
        self.assertEqual(_import(self.path,self.now)['status'],'invalid_data')
        self.insert(self.row(pk=3));self.assertEqual(_import(self.path,self.now)['status'],'available')

    def test_dashboard_is_read_only_and_supervisor_only(self):
        from django.contrib.auth import get_user_model
        user=get_user_model().objects.create(username='mesuef',role='supervisor')
        self.client.force_login(user)
        with patch('token_usage.runtime_context._import',side_effect=AssertionError('GET cannot import')):
            response=self.client.get('/usage/',{'session':self.tracked.pk})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'إشغال السياق — مراقبة صامتة')
        student=get_user_model().objects.create(username='saud',role='student')
        self.client.force_login(student)
        self.assertEqual(self.client.get('/usage/').status_code,403)

    def test_current_filter_and_template_empty_state(self):
        self.insert(self.row());_import(self.path,self.now)
        data=snapshot(self.project,self.tracked.pk,now=self.now)
        self.assertEqual(len(data['sessions']),1)
        html=render_to_string('token_usage/runtime_context.html',{'runtime_context':data})
        self.assertIn('إشغال السياق — مراقبة صامتة',html);self.assertIn('80.0',html)
        other=TrackedSession.objects.create(project=self.project,external_id=str(uuid.uuid4()),name='old')
        self.assertEqual(snapshot(self.project,other.pk,now=self.now)['sessions'],[])

    def test_source_failure_does_not_stop_existing_sync(self):
        from .services import synchronize
        with patch('token_usage.services._synchronize',return_value='billing-sync-ok'):
            self.assertEqual(synchronize(),'billing-sync-ok')

    def test_period_and_source_check_age(self):
        self.insert(self.row(ago=90000));_import(self.path,self.now)
        self.assertEqual(snapshot(self.project,period='day',now=self.now)['sessions'][0]['points'],[])
        self.assertFalse(snapshot(self.project,now=self.now+timedelta(seconds=121))['sessions'][0]['fresh'])

    def test_model_switch_uses_each_observations_own_window_and_turn(self):
        self.insert(self.row(model='model-a',used=800,limit=1000,ago=10))
        _import(self.path,self.now)
        previous_turn=self.turn
        self.turn=str(uuid.uuid4())
        self.insert(self.row(pk=2,model='model-b',used=1500,limit=2000))
        _import(self.path,self.now)
        result=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertEqual(result['percent'],75)
        self.assertEqual(str(result['latest'].turn_id),self.turn)
        self.assertNotEqual(str(result['latest'].turn_id),previous_turn)
        self.assertEqual([(p['model'],p['limit'],p['percent']) for p in result['points']],
                         [('model-a',1000,80),('model-b',2000,75)])
        self.assertEqual(_import(self.path,self.now)['added'],0)
        self.assertEqual(ContextObservation.objects.count(),2)
        self.assertEqual((TokenSample.objects.count(),CapacityEvent.objects.count(),CapacityHandover.objects.count()),(0,0,0))

    def test_compaction_like_drop_preserves_history_without_inventing_event(self):
        # A drop alone is not evidence of compaction. This synthetic fixture checks
        # transport/display behavior only, not real post-compaction accuracy.
        self.insert(self.row(used=900,ago=10));_import(self.path,self.now)
        generation=ContextSource.objects.get().generation
        self.insert(self.row(pk=2,used=100));_import(self.path,self.now)
        result=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertEqual(result['percent'],10)
        self.assertEqual([p['used'] for p in result['points']],[900,100])
        self.assertEqual(ContextSource.objects.get().generation,generation)
        self.assertTrue(all('compaction' not in p for p in result['points']))
        self.assertEqual((TokenSample.objects.count(),CapacityEvent.objects.count(),CapacityHandover.objects.count()),(0,0,0))

    def test_late_old_model_row_does_not_replace_newer_sample(self):
        self.insert(self.row(model='new-model',used=1500,limit=2000))
        _import(self.path,self.now)
        self.insert(self.row(pk=2,model='old-model',used=900,limit=1000,ago=30))
        _import(self.path,self.now)
        result=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertEqual(result['percent'],75)
        self.assertEqual(result['latest'].model,'new-model')
        self.assertEqual(result['age'],0)
        self.assertEqual([p['model'] for p in result['points']],['old-model','new-model'])

    def test_combined_drop_and_model_switch_uses_post_boundary_full_window(self):
        # Counter values from the natural review case (source rows 3113622/3114909).
        # Only the data path is replayed here; no actual model or compaction runs.
        self.insert(self.row(model='gpt-6.1-sol',used=245347,limit=475000,ago=53))
        _import(self.path,self.now)
        self.turn=str(uuid.uuid4())
        self.insert(self.row(pk=2,model='gpt-6-luna',used=35402,limit=258400))
        _import(self.path,self.now)
        result=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertEqual(result['percent'],13.7)
        self.assertEqual((result['latest'].current,result['latest'].limit),(35402,258400))
        self.assertEqual(len(result['points']),2)
        self.assertEqual((TokenSample.objects.count(),CapacityEvent.objects.count(),CapacityHandover.objects.count()),(0,0,0))

    def test_isolated_compaction_counter_pair_same_model_window_and_turn(self):
        # Values from archived natural source rows 3124905/3125701. Identifiers,
        # timestamps and source database are isolated synthetic test fixtures.
        before=self.row(used=452572,limit=475000,model='gpt-6.1-sol',ago=68)
        after=self.row(pk=2,used=42062,limit=475000,model='gpt-6.1-sol')
        for row in [before,after]:
            row['feedback_log_body']=row['feedback_log_body'].replace(
                'auto_compact_scope_limit=Some(900)','auto_compact_scope_limit=Some(450000)')
        before['feedback_log_body']=before['feedback_log_body'].replace(
            'token_limit_reached=false','token_limit_reached=true')
        self.insert(before);_import(self.path,self.now)
        generation=ContextSource.objects.get().generation
        self.insert(after);_import(self.path,self.now)
        result=snapshot(self.project,now=self.now)['sessions'][0]
        self.assertEqual(result['percent'],8.9)
        self.assertEqual([p['used'] for p in result['points']],[452572,42062])
        self.assertEqual({p['model'] for p in result['points']},{'gpt-6.1-sol'})
        self.assertEqual({p['limit'] for p in result['points']},{475000})
        self.assertEqual({p['turn'] for p in result['points']},{self.turn})
        self.assertEqual(ContextSource.objects.get().generation,generation)
        self.assertEqual(_import(self.path,self.now)['added'],0)
        self.assertEqual((TokenSample.objects.count(),CapacityEvent.objects.count(),CapacityHandover.objects.count()),(0,0,0))
