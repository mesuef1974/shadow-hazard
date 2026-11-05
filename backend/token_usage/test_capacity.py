import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import DatabaseError, OperationalError, close_old_connections
from django.utils import timezone
from workspace.models import Project, SessionCharter, ModelPolicy, ProjectDocumentation
from .models import (TrackedSession, TokenSample, SyncRun, ContextSource, ContextObservation,
    CapacityPolicy, CapacityState, CapacityMeasurement, CapacityEvent, CapacityHandover)
from .capacity import (evaluate, snapshot, maintenance, policy_for, thresholds_enabled, digest,
    PARSER, SCHEMA_DIGEST, SOURCE_KIND, text_digest)


def doc(project, task, evidence, pk=None):
    summary='synthetic test authorization only'
    return ProjectDocumentation.objects.create(**({'pk':pk} if pk else {}),project=project,
        session_key='test',task_key=task,summary=summary,evidence=evidence,digest=digest({'summary':summary,'evidence':evidence}))


def policy_record(case, version='synthetic-runtime-enabled', **values):
    defaults=dict(decision=case.decision,decided_by=case.owner,decided_at=case.now,
        source_kind=SOURCE_KIND,parser_version=PARSER,source_schema_digest=SCHEMA_DIGEST,
        thresholds_enabled=True,validation=case.validation)
    defaults.update(values)
    return CapacityPolicy.objects.create(version=version,**defaults)


class CapacityTests(TestCase):
    def test_snapshot_reads_shared_source_once_and_preserves_inspection(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .capacity import inspect, selected_policy
        self.sample(80)
        SessionCharter.objects.create(project=self.project,policy=self.charter.policy,
            key='review',name='Review',scope='bounded',thread_id=str(uuid.uuid4()))
        for missing in (False,True):
            if missing:
                ContextSource.objects.all().delete()
            policy=selected_policy(self.project)
            expected={c.pk:inspect(c,policy,self.now)[2] or None for c in SessionCharter.objects.filter(project=self.project)}
            with CaptureQueriesContext(connection) as queries:
                data=snapshot(self.project,now=self.now)
            self.assertEqual({r['charter_id']:r['uncertain_reason'] for r in data['sessions']},expected)
            self.assertEqual(sum('FROM "token_usage_contextsource"' in q['sql'] for q in queries),1)

    def setUp(self):
        self.owner=get_user_model().objects.create(username='mesuef',role='supervisor')
        self.project=Project.objects.create(name='synthetic runtime')
        model_policy=ModelPolicy.objects.create(category='test')
        self.charter=SessionCharter.objects.create(project=self.project,policy=model_policy,key='backend',
            name='Backend',scope='bounded',thread_id=str(uuid.uuid4()))
        self.tracked=TrackedSession.objects.create(project=self.project,external_id=self.charter.thread_id,name='Current')
        self.now=timezone.now()+timedelta(seconds=10)
        self.decision=doc(self.project,'decision',{'production_activation':True,'synthetic_test_only':True},pk=269)
        self.disabled=policy_for(self.project)
        self.validation=doc(self.project,'validation',{'status':'runtime_capacity_validation_verified',
            'source_kind':SOURCE_KIND,'semantics':'codex_active_context_step_estimate',
            'parser_version':PARSER,'schema_digest':SCHEMA_DIGEST,'original_logs_matched':True,
            'model_and_compaction_verified':True,'activation_authorized':True,
            'model_windows':[{'model':'test-model','full_context_window':100},
                {'model':'test-model','full_context_window':10000},
                {'model':'model-b','full_context_window':200}]})
        self.policy=policy_record(self)
        self.source=ContextSource.objects.create(generation=uuid.uuid4(),identity=text_digest('synthetic source'),
            schema_digest=SCHEMA_DIGEST,status='available',checked_at=self.now)
        self.process='pid:12:'+str(uuid.uuid4())
        self.sequence=0

    def sample(self,context=85,limit=100,model='test-model'):
        self.sequence+=1;self.now+=timedelta(seconds=2)
        turn=uuid.uuid4();source_digest=text_digest(str((self.sequence,context,limit,model)))
        self.source.refresh_from_db()
        key=text_digest(':'.join([str(self.source.generation),self.process,str(self.sequence),self.charter.thread_id,str(turn)]))
        row=ContextObservation.objects.create(key=key,charter=self.charter,thread_id=self.charter.thread_id,
            turn_id=turn,generation=self.source.generation,process_uuid=self.process,source_row=self.sequence,
            source_digest=source_digest,parser_version=PARSER,sampled_at=self.now-timedelta(seconds=2),
            current=context,limit=limit,model=model)
        ContextObservation.objects.filter(pk=row.pk).update(imported_at=self.now-timedelta(seconds=1))
        self.source.checked_at=self.now;self.source.save();row.refresh_from_db()
        return row

    def evaluate(self):
        return evaluate(self.charter.pk,policy=self.policy,now=self.now)

    def test_exact_integer_boundaries_and_typed_handover(self):
        for current in [6999,7000,8499,8500]:
            row=self.sample(current,10000);self.evaluate()
            self.assertEqual(CapacityEvent.objects.filter(kind='warning').count(),int(current>=7000))
            self.assertEqual(CapacityHandover.objects.count(),int(current>=8500))
        handover=CapacityHandover.objects.get();measurement=handover.event.measurement
        self.assertEqual(handover.observation_id,row.pk)
        self.assertEqual(measurement.observation_id,row.pk)
        self.assertIsNone(measurement.sample_id);self.assertIsNone(measurement.sync_id)
        self.assertEqual(measurement.source_generation,self.source.generation)
        self.assertEqual(measurement.evidence['observation']['turn_id'],str(row.turn_id))
        self.assertEqual(measurement.evidence['observation']['parser_version'],PARSER)
        self.assertEqual(handover.payload['measurement']['evidence'],measurement.evidence)
        self.assertEqual(handover.digest,digest(handover.payload))
        self.assertEqual(handover.receipt.evidence['sha256'],handover.digest)

    def test_default_v3_is_disabled_and_not_pinned_to_224(self):
        doc(self.project,'legacy-pin',{'production_activation':False},pk=224)
        self.assertEqual(policy_for(self.project).pk,self.policy.pk)
        self.policy=self.disabled;self.sample(90)
        result=self.evaluate()
        self.assertFalse(result.trusted);self.assertEqual(result.reason,'display_only_runtime')
        self.assertFalse(thresholds_enabled(self.disabled))
        self.assertEqual((CapacityEvent.objects.count(),CapacityHandover.objects.count()),(0,0))

    def test_disabled_policy_cannot_be_activated_by_in_memory_changes(self):
        self.policy=self.disabled;self.policy.thresholds_enabled=True;self.policy.validation=self.validation
        self.sample(90);self.assertEqual(self.evaluate().reason,'display_only_runtime')
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_gate_requires_explicit_approval_and_verified_source_binding(self):
        self.sample(90)
        for changes in [{'activation_authorized':False},{'model_and_compaction_verified':False},
                {'parser_version':'other'},{'schema_digest':'bad'},{'source_kind':'last_request'}]:
            original=self.validation.evidence.copy();self.validation.evidence.update(changes)
            self.validation.digest=digest({'summary':self.validation.summary,'evidence':self.validation.evidence});self.validation.save()
            self.assertEqual(self.evaluate().reason,'display_only_runtime')
            self.validation.evidence=original;self.validation.digest=digest({'summary':self.validation.summary,'evidence':original});self.validation.save()
        self.decision.evidence={'production_activation':False};self.decision.digest=digest({'summary':self.decision.summary,'evidence':self.decision.evidence});self.decision.save()
        self.assertEqual(self.evaluate().reason,'display_only_runtime')
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_direct_jump_duplicates_high_samples_and_reload(self):
        self.sample(90)
        for _ in range(3):self.evaluate()
        self.assertEqual(CapacityMeasurement.objects.count(),1)
        self.source.checked_at=self.now+timedelta(seconds=1);self.source.save();self.now+=timedelta(seconds=1)
        self.policy=CapacityPolicy.objects.get(pk=self.policy.pk);self.evaluate()
        self.assertEqual(CapacityMeasurement.objects.count(),1)
        self.sample(95);self.evaluate()
        self.assertEqual((CapacityEvent.objects.count(),CapacityHandover.objects.count()),(2,1))

    def test_new_policy_evaluates_same_observation_once_and_renders_approved_events(self):
        from django.template.loader import render_to_string
        observation=self.sample(90)
        self.assertEqual(evaluate(self.charter.pk,policy=self.disabled,now=self.now).reason,'display_only_runtime')
        for _ in range(3):self.evaluate()
        data=snapshot(self.project,now=self.now)
        self.assertTrue(data['thresholds_enabled'])
        self.assertEqual(data['sessions'][0]['measurement']['observation_id'],observation.pk)
        self.assertEqual((CapacityEvent.objects.count(),CapacityHandover.objects.count()),(2,1))
        html=render_to_string('registry/_capacity.html',{'capacity_snapshot':data})
        self.assertIn('تنبيه مسجل #',html);self.assertIn('تجهيز مسجل #',html)
        self.assertIn('دون 65٪',html);self.assertIn('دون 80٪',html)
        card=render_to_string('token_usage/runtime_context.html',{'capacity_snapshot':data,'runtime_context':{'sessions':[]}})
        self.assertIn('العتبتان مفعّلتان',card)
        self.assertIn('التجديد التلقائي غير مفعّل',card)
        self.assertNotIn('حدّان مرجعيان معطّلان',card)
        self.assertNotIn('عرض فقط',card)

    def test_hysteresis_65_80_are_strict_and_persisted(self):
        for current in [85,80,85]:self.sample(current);self.evaluate()
        self.assertEqual(CapacityHandover.objects.count(),1)
        for current in [79,85,65,85]:self.sample(current);self.evaluate()
        self.assertEqual(CapacityEvent.objects.filter(kind='warning').count(),1)
        self.assertEqual(CapacityHandover.objects.count(),3)
        self.sample(64);self.evaluate();self.sample(85);self.evaluate()
        self.assertEqual(CapacityEvent.objects.filter(kind='warning').count(),2)
        self.assertEqual(CapacityHandover.objects.count(),4)

    def test_stale_low_and_unavailable_source_never_rearm(self):
        self.sample(85);self.evaluate();self.now+=timedelta(seconds=122)
        low=self.sample(40)
        ContextObservation.objects.filter(pk=low.pk).update(sampled_at=self.now-timedelta(seconds=121))
        self.assertEqual(self.evaluate().reason,'sample_stale')
        self.sample(85);self.evaluate()
        low=self.sample(40);self.source.status='unavailable';self.source.save()
        self.assertEqual(self.evaluate().reason,'source_unavailable')
        self.source.status='available';self.source.save();self.sample(85);self.evaluate()
        self.assertEqual((CapacityEvent.objects.count(),CapacityHandover.objects.count()),(2,1))

    def test_source_missing_unavailable_future_stale_schema(self):
        self.sample()
        cases=[({'status':'unavailable'},'source_unavailable'),
            ({'checked_at':self.now+timedelta(seconds=1)},'source_future'),
            ({'checked_at':self.now-timedelta(seconds=121)},'source_stale'),
            ({'schema_digest':'bad'},'schema_or_parser_unapproved')]
        for changes,reason in cases:
            saved={k:getattr(self.source,k) for k in changes}
            ContextSource.objects.filter(pk=self.source.pk).update(**changes)
            self.assertEqual(self.evaluate().reason,reason)
            ContextSource.objects.filter(pk=self.source.pk).update(**saved)
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_observation_provenance_and_invalid_numbers(self):
        row=self.sample()
        cases=[({'parser_version':'unknown'},'observation_parser_unapproved'),
            ({'source_row':0},'invalid_provenance'),({'source_digest':'bad'},'invalid_provenance'),
            ({'sampled_at':self.now+timedelta(seconds=1)},'invalid_provenance'),
            ({'imported_at':self.now+timedelta(seconds=1)},'invalid_provenance'),
            ({'imported_at':row.sampled_at-timedelta(seconds=1)},'invalid_provenance'),
            ({'key':'bad'},'invalid_observation_key'),({'model':''},'model_unconfirmed'),
            ({'current':0},'invalid_capacity'),({'limit':0},'invalid_capacity')]
        for changes,reason in cases:
            saved={k:getattr(row,k) for k in changes}
            ContextObservation.objects.filter(pk=row.pk).update(**changes)
            self.assertEqual(self.evaluate().reason,reason)
            ContextObservation.objects.filter(pk=row.pk).update(**saved)
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_no_tokensample_fallback_even_if_fresh_high_usage_exists(self):
        TokenSample.objects.create(session=self.tracked,source_id=1,sampled_at=self.now,total=1000,
            input=900,cached=0,output=100,reasoning=0,context=100,context_limit=100)
        SyncRun.objects.create(success=True,finished_at=self.now)
        self.source.delete()
        self.assertEqual(self.evaluate().reason,'source_missing')
        self.assertEqual(CapacityEvent.objects.count(),0)
        self.assertEqual(TokenSample.objects.get().total,1000)

    def test_generation_change_rejects_old_observation_and_does_not_reset_cycles(self):
        self.sample(85);self.evaluate()
        self.source.generation=uuid.uuid4();self.source.save()
        self.assertEqual(self.evaluate().reason,'current_generation_observation_missing')
        self.sample(85);self.evaluate()
        self.assertEqual(CapacityHandover.objects.count(),1)
        self.sample(64);self.evaluate();self.sample(85);self.evaluate()
        self.assertEqual(CapacityHandover.objects.count(),2)

    def test_binding_project_and_conflicts(self):
        self.sample(85)
        self.charter.thread_id=str(uuid.uuid4());self.charter.save()
        self.assertEqual(self.evaluate().reason,'current_generation_observation_missing')
        self.charter.thread_id=self.tracked.external_id;self.charter.save()
        SessionCharter.objects.create(project=self.project,policy=self.charter.policy,key='duplicate',thread_id=self.charter.thread_id)
        self.assertEqual(self.evaluate().reason,'conflicting_binding')
        other=Project.objects.create(name='other');self.decision.project=other;self.decision.save()
        with self.assertRaises(ValueError):self.evaluate()
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_conflicting_same_timestamp_and_late_source_order(self):
        first=self.sample(90);second=self.sample(40)
        ContextObservation.objects.filter(pk=second.pk).update(sampled_at=first.sampled_at)
        self.assertEqual(self.evaluate().reason,'conflicting_observations')
        ContextObservation.objects.filter(pk=second.pk).update(sampled_at=first.sampled_at-timedelta(seconds=1))
        self.assertEqual(self.evaluate().reason,'source_order_conflict')
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_watermark_blocks_old_observation_after_generation_change(self):
        first=self.sample(85);self.evaluate()
        self.source.generation=uuid.uuid4();self.source.save()
        other=self.sample(40)
        ContextObservation.objects.filter(pk=other.pk).update(sampled_at=first.sampled_at-timedelta(seconds=1))
        self.assertEqual(self.evaluate().reason,'older_than_processed')
        self.sample(85);self.evaluate()
        self.assertEqual(CapacityEvent.objects.count(),2)

    def test_model_window_changes_need_validation_and_never_implicitly_rearm(self):
        self.sample(85);self.evaluate()
        self.sample(190,200,'model-b');self.evaluate()
        self.assertEqual(CapacityHandover.objects.count(),1)
        self.sample(90,100,'unknown-model')
        self.assertEqual(self.evaluate().reason,'model_window_unvalidated')
        self.sample(180,201,'model-b')
        self.assertEqual(self.evaluate().reason,'model_window_unvalidated')
        self.sample(60,200,'model-b');self.evaluate()
        self.sample(180,200,'model-b');self.evaluate()
        self.assertEqual((CapacityEvent.objects.count(),CapacityHandover.objects.count()),(4,2))

    def test_legacy_measurements_and_events_are_preserved_but_hidden(self):
        legacy=policy_record(self,'legacy',source_kind='last_request',thresholds_enabled=False)
        state=CapacityState.objects.create(charter=self.charter,policy=legacy,thread_id=self.charter.thread_id)
        token=TokenSample.objects.create(session=self.tracked,source_id=1,sampled_at=self.now,
            total=10,input=9,cached=0,output=1,reasoning=0,context=90,context_limit=100)
        sync=SyncRun.objects.create(success=True,finished_at=self.now)
        m=CapacityMeasurement.objects.create(key='historical',state=state,sample=token,sync=sync,
            trusted=True,reason='',current=90,limit=100,percent=90,observed_at=self.now)
        event=CapacityEvent.objects.create(key='historical',measurement=m,kind='prepare')
        receipt=doc(self.project,'historical',{'historical':True})
        h=CapacityHandover.objects.create(event=event,payload={'historical':True},digest='preserved',receipt=receipt)
        self.policy=policy_record(self,'runtime-disabled',thresholds_enabled=False)
        self.sample(90);self.evaluate();data=snapshot(self.project,now=self.now)
        self.assertFalse(data['thresholds_enabled']);self.assertIsNone(data['sessions'][0]['recent_preparation'])
        m.refresh_from_db();h.refresh_from_db()
        self.assertEqual((m.sample_id,m.sync_id,m.observation_id),(token.pk,sync.pk,None))
        self.assertEqual(h.payload,{'historical':True});self.assertIsNone(h.observation_id)
        self.assertEqual(CapacityEvent.objects.count(),1)

    def test_old_source_policy_rejected_even_when_flag_true(self):
        self.policy=policy_record(self,'legacy-flag',source_kind='last_request')
        self.sample(90);self.assertEqual(self.evaluate().reason,'source_kind_unapproved')
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_documentation_bundle_and_invalid_receipt_fail_atomically_then_recover(self):
        self.sample()
        for target in ['token_usage.capacity.document','token_usage.capacity.CapacityHandover.objects.create']:
            with patch(target,side_effect=DatabaseError('injected failure')):
                with self.assertRaises(DatabaseError):self.evaluate()
            self.assertEqual((CapacityMeasurement.objects.count(),CapacityEvent.objects.count(),CapacityHandover.objects.count()),(0,0,0))
            self.assertFalse(CapacityState.objects.exists())
        with patch('token_usage.capacity.document',return_value=self.decision):
            with self.assertRaises(ValueError):self.evaluate()
        self.assertEqual(CapacityEvent.objects.count(),0)
        self.evaluate();self.assertEqual(CapacityHandover.objects.count(),1)

    def test_receipt_evidence_is_frozen_as_source_changes(self):
        row=self.sample();self.evaluate();handover=CapacityHandover.objects.get()
        old=handover.payload.copy()
        self.source.generation=uuid.uuid4();self.source.checked_at=self.now+timedelta(seconds=1);self.source.save()
        handover.refresh_from_db()
        self.assertEqual(handover.payload,old)
        self.assertEqual(handover.observation_id,row.pk)
        self.assertEqual(handover.payload['measurement']['evidence']['source']['generation'],str(row.generation))

    def test_passive_only_with_explicit_side_effect_mocks(self):
        from contextlib import ExitStack
        self.sample()
        targets=['workspace.models.ModelJob.objects.create','execution_context.models.SessionDispatch.objects.create',
            'execution_context.models.SessionMessage.objects.create','workspace.models.SessionCharter.save',
            'token_usage.models.TrackedSession.save','execution_context.delivery.deliver_one','workspace.dispatcher.run_one',
            'workspace.wakeup.DispatcherWakeup','workspace.wakeup.signal_dispatcher','execution_context.owner_tools.OwnerTools.call','subprocess.run']
        with ExitStack() as stack:
            mocks=[stack.enter_context(patch(target)) for target in targets]
            self.evaluate();data=snapshot(self.project,now=self.now)
            for mock in mocks:mock.assert_not_called()
        self.assertTrue(data['sessions'][0]['trusted'])
        self.assertEqual(data['source_kind'],SOURCE_KIND)
        self.assertIsNotNone(data['sessions'][0]['recent_preparation']['receipt_id'])
        self.assertEqual(data['sessions'][0]['measurement']['evidence']['observation']['thread_id'],self.charter.thread_id)

    def test_maintenance_survives_errors(self):
        with patch('token_usage.capacity.evaluate',side_effect=DatabaseError):result=maintenance()
        self.assertEqual(result['errors'][0]['error_type'],'DatabaseError')

    def test_worker_maintenance_invokes_passive_evaluator_and_logs_failure(self):
        from contextlib import ExitStack
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from django.core.management import call_command
        from workspace.models import DispatcherHeartbeat
        prefix='workspace.management.commands.run_model_dispatcher.'
        with TemporaryDirectory() as folder, self.settings(BASE_DIR=Path(folder)), ExitStack() as stack:
            # TestCase owns this connection's transaction; a worker's lifecycle
            # cleanup must not close it while testing maintenance dispatch.
            stack.enter_context(patch(prefix+'close_old_connections'))
            for name in ['observe_arrivals','reconcile','deliver_one','run_one']:
                stack.enter_context(patch(prefix+name,return_value=None))
            stack.enter_context(patch('execution_context.owner_delivery.recover_unstarted_claims'))
            stack.enter_context(patch(prefix+'verify_owner_closures',return_value={'rejected':[]}))
            stack.enter_context(patch(prefix+'ReadonlyRunner'))
            stack.enter_context(patch(prefix+'threading.Thread'))
            wake=stack.enter_context(patch(prefix+'DispatcherWakeup'))
            wake.return_value.wait.side_effect=KeyboardInterrupt()
            stack.enter_context(patch(prefix+'automation_cycle',return_value={'errors':[]}))
            evaluate_mock=stack.enter_context(patch('token_usage.capacity.maintenance',return_value={
                'evaluated':0,'errors':[{'operation':'capacity','error_type':'DatabaseError'}]}))
            call_command('run_model_dispatcher',watch=True)
            evaluate_mock.assert_called_once()
        heartbeat=DispatcherHeartbeat.objects.get(key='local')
        self.assertEqual(heartbeat.state,'stopped')
        self.assertIn('capacity: DatabaseError',heartbeat.last_error)


class CapacityViewContextTests(TestCase):
    setUp=CapacityTests.setUp
    sample=CapacityTests.sample
    urls=['/sessions/','/sessions/automation/','/usage/']  # the automation map no longer shows the Codex card

    def test_snapshot_contract_without_evaluation_or_fallback(self):
        from contextlib import ExitStack
        policy_record(self,'view-disabled',thresholds_enabled=False)
        self.sample(90);payload=snapshot(self.project,now=self.now)
        self.client.force_login(self.owner)
        with ExitStack() as stack:
            workspace_snapshot=stack.enter_context(patch('workspace.views.capacity_snapshot',return_value=payload))
            usage_snapshot=stack.enter_context(patch('token_usage.views.capacity_snapshot',return_value=payload))
            evaluate_mock=stack.enter_context(patch('token_usage.capacity.evaluate'))
            maintenance_mock=stack.enter_context(patch('token_usage.capacity.maintenance'))
            sync_mock=stack.enter_context(patch('token_usage.views.synchronize'))
            for url in self.urls:
                response=self.client.get(url);self.assertEqual(response.status_code,200,url)
                self.assertEqual(response.context['capacity_snapshot'],payload)
                self.assertFalse(response.context['capacity_snapshot']['thresholds_enabled'])
                self.assertEqual(response.context['capacity_snapshot']['source_kind'],SOURCE_KIND)
                self.assertContains(response,'تقدير السياق عند نهاية الخطوة')
            self.assertEqual(workspace_snapshot.call_count,2);usage_snapshot.assert_called_once_with(self.project)
            evaluate_mock.assert_not_called();maintenance_mock.assert_not_called();sync_mock.assert_not_called()
        self.assertEqual(CapacityEvent.objects.count(),0)

    def test_access_controls_run_before_snapshot(self):
        with patch('workspace.views.capacity_snapshot') as workspace_snapshot,patch('token_usage.views.capacity_snapshot') as usage_snapshot:
            for url in self.urls:self.assertEqual(self.client.get(url).status_code,302)
            student=get_user_model().objects.create(username='saud',role='student');self.client.force_login(student)
            for url in self.urls:self.assertEqual(self.client.get(url).status_code,403)
            workspace_snapshot.assert_not_called();usage_snapshot.assert_not_called()


class CapacityRaceTests(TransactionTestCase):
    def test_concurrent_evaluations_only_one_handover_then_retry(self):
        setup=CapacityTests();setup.setUp();setup.sample()
        barrier=Barrier(2)
        def run(_):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try:return evaluate(setup.charter.pk,policy=setup.policy,now=setup.now).pk
                except OperationalError:return None
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,range(2)))
        setup.evaluate()
        self.assertEqual(CapacityMeasurement.objects.count(),1)
        self.assertEqual(CapacityHandover.objects.count(),1)
        self.assertEqual(CapacityEvent.objects.count(),2)
