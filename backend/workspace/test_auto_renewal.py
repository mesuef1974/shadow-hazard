import copy
import json
import os
import uuid
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from workspace.models import (AutoRenewalLatch, AutoRenewalPolicy, Decision, DocumentAccess,
    ModelPolicy, Phase, Project, ProjectDocumentation, SessionCharter, Task)
from workspace.memory import document
from workspace.auto_renewal import (AUTO_RENEWAL_SCOPES, AutoRenewalError, activate_policy,
    activation_valid, bounded_transfer, execution_allowed, latch_handover, maintenance,
    policy_for, reconcile)
from token_usage.models import (CapacityHandover, CapacityPolicy, ContextObservation, ContextSource)
from token_usage.models import CapacityEvent
from token_usage.capacity import (evaluate, digest, PARSER, SCHEMA_DIGEST, SOURCE_KIND, thresholds_enabled)
from token_usage.runtime_context import digest as text_digest
from execution_context.owner_tools import OwnerTools, OwnerToolsError


def tool_schemas():
    return {
        'create_thread': {'inputSchema': {'required': ['prompt', 'target'], 'properties': {
            'prompt': {'type': 'string'}, 'target': {'anyOf': [{'required': ['type', 'projectId', 'environment'],
                'properties': {'type': {'enum': ['project']}, 'projectId': {'type': 'string'},
                    'environment': {'anyOf': [{'properties': {'type': {'enum': ['local']}}}]}}}]}}}},
        'read_thread': {'inputSchema': {'required': ['threadId'], 'properties': {k: {} for k in
            ['threadId', 'hostId', 'turnLimit', 'includeOutputs', 'maxOutputCharsPerItem']}}},
        'list_projects': {'inputSchema': {'properties': {}}},
    }


class AutoRenewalFixture:
    def setUp(self):
        self.owner = get_user_model().objects.create(username='mesuef', role='supervisor')
        self.project = Project.objects.create(name='synthetic automatic renewal')
        self.model_policy = ModelPolicy.objects.create(category='coordination')
        self.charter = SessionCharter.objects.create(project=self.project, policy=self.model_policy,
            key='coordination', name='coordination', scope='bounded coordination scope',
            rules='mandatory coordination rules', thread_id=str(uuid.uuid4()), state='active')
        self.source_thread = self.charter.thread_id
        self.now = timezone.now() + timedelta(seconds=5)
        self.decision_doc = self.doc('threshold-decision', {'production_activation': True})
        self.validation_doc = self.doc('threshold-validation', {
            'status': 'runtime_capacity_validation_verified', 'source_kind': SOURCE_KIND,
            'semantics': 'codex_active_context_step_estimate', 'parser_version': PARSER,
            'schema_digest': SCHEMA_DIGEST, 'original_logs_matched': True,
            'model_and_compaction_verified': True, 'activation_authorized': True,
            'model_windows': [{'model': 'test-model', 'full_context_window': 100}]})
        self.capacity_policy = CapacityPolicy.objects.create(version='synthetic-auto-runtime',
            decision=self.decision_doc, validation=self.validation_doc, decided_by=self.owner,
            decided_at=self.now, source_kind=SOURCE_KIND, parser_version=PARSER,
            source_schema_digest=SCHEMA_DIGEST, thresholds_enabled=True)
        self.generation = uuid.uuid4()
        self.process = 'pid:99:' + str(uuid.uuid4())
        self.source = ContextSource.objects.create(generation=self.generation,
            identity='a' * 64, schema_digest=SCHEMA_DIGEST, status='available', checked_at=self.now-timedelta(seconds=1))
        self.observation = self.add_observation(85, 1)
        self.measurement = evaluate(self.charter.pk, policy=self.capacity_policy, now=self.now)
        self.handover = CapacityHandover.objects.filter(event__measurement=self.measurement).first()
        if not self.handover:
            self.fail(f'no handover: trusted={self.measurement.trusted} reason={self.measurement.reason} '
                f'percent={self.measurement.percent} enabled={thresholds_enabled(self.capacity_policy)} '
                f'events={list(CapacityEvent.objects.filter(measurement=self.measurement).values_list("kind", flat=True))}')
        self.auto_policy = policy_for(self.charter)
        self.assertFalse(self.auto_policy.enabled)

    def doc(self, task_key, evidence, summary='synthetic evidence', *, session_key=None):
        payload = {'summary': summary, 'evidence': evidence}
        return ProjectDocumentation.objects.create(project=self.project, session_key=session_key or self.charter.key,
            task_key=task_key, summary=summary, evidence=evidence, digest=digest(payload))

    def add_observation(self, current, source_row, *, charter=None):
        charter = charter or self.charter
        turn = uuid.uuid4()
        sampled = timezone.now() - timedelta(seconds=2)
        key = text_digest(':'.join([str(self.generation), self.process, str(source_row), charter.thread_id, str(turn)]))
        row = ContextObservation.objects.create(key=key, charter=charter,
            thread_id=charter.thread_id, turn_id=turn, generation=self.generation,
            process_uuid=self.process, source_row=source_row, source_digest=digest(str((source_row,current)),),
            parser_version=PARSER, sampled_at=sampled, current=current, limit=100,
            model='test-model')
        imported = sampled + timedelta(seconds=1)
        ContextObservation.objects.filter(pk=row.pk).update(imported_at=imported)
        self.source.checked_at = imported
        self.source.save()
        row.refresh_from_db()
        return row

    def activation(self, charter=None, *, default=None, caller_thread_id=None):
        charter = charter or self.charter
        if not hasattr(self, 'broker'):
            self.broker = str(uuid.uuid4())
            self.broker_charter = SessionCharter.objects.create(project=self.project, policy=self.model_policy,
                key='owner-broker', name='owner broker', scope='owner scope', rules='rules',
                thread_id=self.broker, state='active')
        self.config = {'project_id': self.project.pk, 'authorizing_user_id': self.owner.pk,
            'caller_thread_id': caller_thread_id or self.broker, 'server_sha256': 'b' * 64,
            'owner_identity': {'sha256': 'c' * 64}, 'pipe': 'fake-owner-pipe'}
        evidence = {'status': 'automatic_renewal_activation_authorized', 'automatic': True,
            'scope': AUTO_RENEWAL_SCOPES[charter.key], 'charter_id': charter.pk,
            'policy_id': policy_for(charter).pk, 'owner_id': self.owner.pk,
            'binding_at_authorization': charter.thread_id,
            'allow_create': True, 'allow_rebind': True, 'transport': 'owner-desktop-default',
            'desktop_default_inheritance_authorized': True,
            'default_profile_observed': True,
            'expected_desktop_default': default or {'model': 'gpt-6-sol', 'thinking': 'high'},
            'owner_channel': {'caller_thread_id': self.broker,
                'server_sha256': self.config['server_sha256'],
                'owner_image_sha256': self.config['owner_identity']['sha256']}}
        receipt = self.doc(f'auto-activation:{charter.key}', evidence,
            'explicit synthetic owner authorization', session_key=charter.key)
        return receipt

    def add_session(self, key, name, *, thread_id=None, current=85, source_row=10):
        charter = SessionCharter.objects.create(project=self.project, policy=self.model_policy,
            key=key, name=name, scope=f'{key} bounded scope', rules=f'{key} required rules',
            thread_id=thread_id or str(uuid.uuid4()), state='active')
        policy = policy_for(charter)
        observation = self.add_observation(current, source_row, charter=charter)
        measurement = evaluate(charter.pk, policy=self.capacity_policy, now=self.now)
        handover = CapacityHandover.objects.filter(event__measurement=measurement).first()
        self.assertTrue(handover, f'expected fresh {key} capacity handover')
        return charter, policy, observation, handover


class AutoRenewalPolicyTests(AutoRenewalFixture, TestCase):
    def test_default_off_latches_one_source_and_survives_drop(self):
        first = latch_handover(self.handover.pk, now=self.now)
        second = latch_handover(self.handover.pk, now=self.now)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.state, 'latched_disabled')
        self.add_observation(20, 2)
        evaluate(self.charter.pk, policy=self.capacity_policy, now=self.now)
        maintenance()
        self.assertEqual(AutoRenewalLatch.objects.filter(charter=self.charter).count(), 1)
        self.assertFalse(self.auto_policy.enabled)
        self.assertIsNone(first.renewal_id)

    def test_stale_handover_is_not_latched_and_binding_change_invalidates_old_latch(self):
        self.assertIsNone(latch_handover(self.handover.pk, now=self.now+timedelta(seconds=180)))
        row = latch_handover(self.handover.pk, now=self.now)
        self.charter.thread_id = str(uuid.uuid4())
        self.charter.save(update_fields=['thread_id'])
        maintenance()
        row.refresh_from_db()
        self.assertEqual(row.state, 'invalidated')

    def test_bare_enabled_bool_and_manual_false_receipt_cannot_authorize_auto(self):
        self.auto_policy.enabled = True
        self.auto_policy.owner = self.owner
        self.auto_policy.save(update_fields=['enabled','owner','updated_at'])
        self.auto_policy.refresh_from_db()
        self.assertFalse(activation_valid(self.auto_policy))
        manual = self.doc('manual-only', {'automatic': False, 'allow_create': True})
        self.auto_policy.activation_receipt = manual
        self.auto_policy.save(update_fields=['activation_receipt','updated_at'])
        self.assertFalse(activation_valid(self.auto_policy))

    def test_only_explicit_scoped_receipt_can_activate_and_transfer_is_bounded_db_data(self):
        receipt = self.activation()
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            activated = activate_policy(self.auto_policy.pk, receipt.pk)
            self.assertTrue(activation_valid(activated))
        latch = latch_handover(self.handover.pk, now=self.now)
        phase = Phase.objects.create(project=self.project, title='Current phase', objective='bounded', order=1)
        Task.objects.create(phase=phase, title='Current work', description='active task from DB',
            assignee=self.owner, creator=self.owner, state='doing')
        Decision.objects.create(project=self.project, title='Current decision', body='real decision reference',
            author=self.owner, private=False)
        DocumentAccess.objects.create(path='docs/decisions/example.md', team_visible=True)
        document(self.project, self.charter.key, 'task:current-work', 'تنفيذ اختبار التسليم الأخير',
            {'status': 'verified', 'synthetic': True})
        transfer = bounded_transfer(latch)
        self.assertIn('Current work', transfer.summary)
        self.assertIn('Current decision', transfer.summary)
        self.assertIn('docs/decisions/example.md', transfer.summary)
        self.assertIn('تنفيذ اختبار التسليم الأخير', transfer.summary)
        self.assertNotIn(self.charter.rules, transfer.summary)
        self.assertLessEqual(len(transfer.summary), 2000)
        original_summary = transfer.summary
        transfer.delete()
        document(self.project, self.charter.key, 'task:newer-work', 'معلومة أحدث لا تغير إيصال النقل المحفوظ',
            {'status': 'verified', 'synthetic': True})
        resumed = bounded_transfer(latch)
        self.assertEqual(resumed.summary, original_summary)

    @override_settings(AUTO_RENEWAL_EXECUTION_ENABLED=False)
    def test_execution_gate_stays_off_even_with_activation_receipt(self):
        receipt = self.activation()
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            activate_policy(self.auto_policy.pk, receipt.pk)
        row = latch_handover(self.handover.pk, now=self.now)
        self.assertEqual(row.state, 'latched_disabled')
        self.assertEqual(maintenance()['advanced'], 0)
        self.assertFalse(row.renewal_id)


class AutoRenewalSessionScopeTests(AutoRenewalFixture, TestCase):
    def test_backend_requires_a_distinct_owner_and_its_own_scoped_approval(self):
        backend = self.add_session('backend', 'الباك إند', source_row=14)[0]
        receipt = self.activation(backend)
        backend_config = copy.deepcopy(self.config)
        backend_config['caller_thread_id'] = backend.thread_id
        with patch('execution_context.owner_tools.load_config', return_value=backend_config):
            with self.assertRaises(AutoRenewalError):
                activate_policy(policy_for(backend).pk, receipt.pk)
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            policy = activate_policy(policy_for(backend).pk, receipt.pk)
            self.assertTrue(activation_valid(policy))
            self.assertEqual(policy.activation_receipt.evidence['scope'], 'backend_default')

    def add_front_review(self):
        frontend = self.add_session('frontend', 'واجهة المشروع', source_row=11)
        review = self.add_session('review', 'مراجعة المشروع', source_row=12)
        return frontend, review

    def activate(self, charter, default):
        receipt = self.activation(charter, default=default)
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            policy = activate_policy(policy_for(charter).pk, receipt.pk)
            self.assertTrue(activation_valid(policy))
        return policy, receipt

    def test_cross_scope_activation_receipt_is_rejected(self):
        frontend, _ = self.add_front_review()
        coordination_receipt = self.activation(self.charter)
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            with self.assertRaises(AutoRenewalError):
                activate_policy(policy_for(frontend[0]).pk, coordination_receipt.pk)
        self.assertFalse(policy_for(frontend[0], create=False).enabled)

    def test_wrong_scope_alone_cannot_authorize_an_otherwise_matching_receipt(self):
        frontend, _ = self.add_front_review()
        receipt = self.activation(frontend[0])
        evidence = copy.deepcopy(receipt.evidence)
        evidence['scope'] = AUTO_RENEWAL_SCOPES['coordination']
        wrong_scope = self.doc('frontend-wrong-scope-only', evidence, session_key='frontend')
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            with self.assertRaises(AutoRenewalError):
                activate_policy(policy_for(frontend[0]).pk, wrong_scope.pk)
        self.assertFalse(policy_for(frontend[0], create=False).enabled)

    @override_settings(AUTO_RENEWAL_EXECUTION_ENABLED=True)
    def test_three_sessions_have_independent_scoped_policies_receipts_and_latches(self):
        frontend, review = self.add_front_review()
        sessions = [(self.charter, self.handover, {'model': 'gpt-6-sol', 'thinking': 'high'}),
            (frontend[0], frontend[3], {'model': 'gpt-6-luna', 'thinking': 'low'}),
            (review[0], review[3], {'model': 'gpt-6.1-sol', 'thinking': 'medium'})]
        policies = []
        receipts = []
        for charter, _, default in sessions:
            policy, receipt = self.activate(charter, default)
            policies.append(policy)
            receipts.append(receipt)
        self.assertEqual(len({policy.pk for policy in policies}), 3)
        self.assertEqual(len({receipt.pk for receipt in receipts}), 3)
        for policy, receipt, (charter, handover, default) in zip(policies, receipts, sessions):
            self.assertEqual(policy.charter_id, charter.pk)
            self.assertEqual(policy.activation_receipt_id, receipt.pk)
            self.assertEqual(receipt.evidence['scope'], AUTO_RENEWAL_SCOPES[charter.key])
            self.assertEqual(receipt.evidence['policy_id'], policy.pk)
            self.assertEqual(receipt.evidence['expected_desktop_default'], default)
            with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
                latch = latch_handover(handover.pk, now=self.now)
            self.assertEqual(latch.charter_id, charter.pk)
            self.assertEqual(latch.state, 'ready')
        self.assertEqual(AutoRenewalLatch.objects.filter(charter__in=[s[0] for s in sessions]).count(), 3)
        from token_usage.capacity import snapshot
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            state = snapshot(self.project)
        self.assertTrue(state['automatic_renewal_enabled'])
        self.assertCountEqual(state['automatic_renewal_sessions'], [charter.name for charter, _, _ in sessions])

    def test_one_busy_session_does_not_stop_other_session_consumers(self):
        frontend, review = self.add_front_review()
        sessions = [(self.charter, self.handover, {'model': 'gpt-6-sol', 'thinking': 'high'}),
            (frontend[0], frontend[3], {'model': 'gpt-6-luna', 'thinking': 'low'}),
            (review[0], review[3], {'model': 'gpt-6.1-sol', 'thinking': 'medium'})]
        for charter, _, default in sessions:
            self.activate(charter, default)
        visited = []

        def run_one(row):
            visited.append(row.charter.key)
            if row.charter.key == 'frontend':
                raise AutoRenewalError('source lane is busy')
            return row

        with override_settings(AUTO_RENEWAL_EXECUTION_ENABLED=True), \
                patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)), \
                patch('workspace.auto_renewal._run_claimed', side_effect=run_one):
            result = maintenance()
        self.assertCountEqual(visited, ['coordination', 'frontend', 'review'])
        self.assertEqual(result['advanced'], 2)
        self.assertIn({'operation': 'execute', 'session_key': 'frontend',
            'latch_id': AutoRenewalLatch.objects.get(charter=frontend[0]).pk,
            'error_type': 'AutoRenewalError'}, result['errors'])
        self.assertEqual(AutoRenewalLatch.objects.get(charter=frontend[0]).state, 'preintent_backoff')
        self.assertEqual(AutoRenewalLatch.objects.filter(renewal__isnull=False).count(), 0)

    def test_backend_owner_caller_is_dynamically_excluded_from_target_activation(self):
        self.activation(self.charter)
        backend = SessionCharter.objects.create(project=self.project, policy=self.model_policy,
            key='backend', name='backend', scope='owner caller', rules='never target the caller',
            thread_id=str(uuid.uuid4()), state='active')
        frontend = self.add_session('frontend', 'واجهة تطابق قناة المالك', source_row=13)[0]
        self.config['caller_thread_id'] = frontend.thread_id
        self.assertFalse(policy_for(backend).enabled)
        frontend_policy = policy_for(frontend)
        receipt = self.activation(frontend, caller_thread_id=frontend.thread_id)
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            with self.assertRaises(AutoRenewalError):
                activate_policy(frontend_policy.pk, receipt.pk)
            frontend_policy.owner = self.owner
            frontend_policy.activation_receipt = receipt
            frontend_policy.enabled = True
            frontend_policy.save(update_fields=['owner', 'activation_receipt', 'enabled', 'updated_at'])
            self.assertFalse(execution_allowed(frontend_policy))
            with override_settings(AUTO_RENEWAL_EXECUTION_ENABLED=True):
                result = maintenance()
        self.assertTrue(frontend_policy.enabled)  # A forced DB flag is still rejected by the dynamic caller guard.
        self.assertFalse(AutoRenewalLatch.objects.filter(charter=frontend).exists())
        self.assertEqual(result['advanced'], 0)

    def test_fresh_low_reading_preserves_the_same_latch_and_deduplicates_all_three(self):
        frontend, review = self.add_front_review()
        sessions = [(self.charter, self.handover), (frontend[0], frontend[3]), (review[0], review[3])]
        for index, (charter, handover) in enumerate(sessions):
            first = latch_handover(handover.pk, now=self.now)
            duplicate = latch_handover(handover.pk, now=self.now)
            self.assertEqual(first.pk, duplicate.pk)
            self.add_observation(20, 20 + index, charter=charter)
            evaluate(charter.pk, policy=self.capacity_policy, now=self.now)
            after_low = latch_handover(handover.pk, now=self.now)
            self.assertEqual(first.pk, after_low.pk)
            self.assertEqual(AutoRenewalLatch.objects.filter(charter=charter).count(), 1)


class AutoRenewalClaimConcurrencyTests(AutoRenewalFixture, TransactionTestCase):
    @override_settings(AUTO_RENEWAL_EXECUTION_ENABLED=True)
    def test_simultaneous_claims_have_one_winner(self):
        from workspace.auto_renewal import _claim
        receipt = self.activation()
        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            activate_policy(self.auto_policy.pk, receipt.pk)
            latch = latch_handover(self.handover.pk, now=self.now)
        barrier = Barrier(2)

        def claim():
            close_old_connections()
            barrier.wait()
            try:
                return _claim(latch.pk, timezone.now())
            finally:
                close_old_connections()

        with patch('execution_context.owner_tools.load_config', return_value=copy.deepcopy(self.config)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [future.result() for future in [pool.submit(claim), pool.submit(claim)]]
        self.assertEqual(sum(result is not None for result in results), 1)
        latch.refresh_from_db()
        self.assertEqual(latch.state, 'claimed')


class AutoRenewalDesktopIntegrationTests(AutoRenewalFixture, TestCase):
    """Runs the production auto consumer/saga through a fake Desktop RPC boundary."""
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.folder = TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.stack.enter_context(patch.dict(os.environ, {'CODEX_HOME': self.folder.name}))
        super().setUp()
        self.broker = str(uuid.uuid4()); self.target = str(uuid.uuid4()); self.app_project = str(uuid.uuid4())
        self.broker_charter = SessionCharter.objects.create(project=self.project, policy=self.model_policy,
            key='owner-broker', name='owner broker', scope='owner scope', rules='rules',
            thread_id=self.broker, state='active')
        self.config = {'project_id': self.project.pk, 'authorizing_user_id': self.owner.pk,
            'caller_thread_id': self.broker, 'server_sha256': 'b' * 64,
            'owner_identity': {'sha256': 'c' * 64}, 'pipe': 'fake-owner-pipe'}
        self.tools = self.tool_schemas(); self.rpc_calls = []; self.states = {}
        self.create_error = None
        self.inject_busy_after_second_lookup = False
        self.project_lookup_calls = 0
        self.busy_job = None
        self.latest = {self.source_thread: str(uuid.uuid4()), self.broker: str(uuid.uuid4())}
        self.project_rows = [{'projectId': self.app_project, 'projectKind': 'local',
            'hostId': 'local', 'path': str(settings.BASE_DIR.parent.resolve())}]
        self.write_source()
        for module in ['workspace.renewal_desktop', 'execution_context.renewal_owner_tools',
                       'execution_context.owner_tools']:
            self.stack.enter_context(patch(module + '.load_config', side_effect=lambda: copy.deepcopy(self.config)))
        self.stack.enter_context(patch('execution_context.renewal_owner_tools.pipe_identity',
            side_effect=lambda pipe: copy.deepcopy(self.config['owner_identity'])))
        def initialize(client, config=None, timeout=30):
            client.config = copy.deepcopy(config or self.config); client.tools = copy.deepcopy(self.tools)
        self.stack.enter_context(patch.object(OwnerTools, '__init__', new=initialize))
        self.stack.enter_context(patch.object(OwnerTools, 'close'))
        self.rpc_mock = self.stack.enter_context(patch.object(OwnerTools, 'rpc', autospec=True, side_effect=self.rpc))
        self.activation_receipt = self.activation()
        self.stack.enter_context(override_settings(AUTO_RENEWAL_EXECUTION_ENABLED=True))
        self.stack.enter_context(override_settings(SESSION_RENEWAL_TEST_ADAPTERS=False))
        activate_policy(self.auto_policy.pk, self.activation_receipt.pk)
        self.latch = latch_handover(self.handover.pk, now=self.now)

    @staticmethod
    def tool_schemas():
        return {
            'create_thread': {'inputSchema': {'required': ['prompt', 'target'], 'properties': {
                'prompt': {'type': 'string'}, 'target': {'anyOf': [{'required': ['type','projectId','environment'],
                    'properties': {'type': {'enum':['project']}, 'projectId': {'type':'string'},
                        'environment': {'anyOf':[{'properties':{'type':{'enum':['local']}}}]}}}]}}}},
            'read_thread': {'inputSchema': {'required': ['threadId'], 'properties': {k: {} for k in
                ['threadId','hostId','turnLimit','includeOutputs','maxOutputCharsPerItem']}}},
            'list_projects': {'inputSchema': {'properties': {}}},
        }

    def path(self, thread):
        folder = Path(self.folder.name)/'sessions'/'fixture'; folder.mkdir(parents=True, exist_ok=True)
        return folder/f'rollout-{thread}.jsonl'

    def save(self, thread, records):
        self.path(thread).write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records)+'\n', encoding='utf-8')

    def write_source(self):
        turn = self.latest[self.source_thread]; stamp = timezone.now().timestamp()-20
        self.save(self.source_thread, [
            {'type':'session_meta','payload':{'session_id':self.source_thread,'cwd':str(settings.BASE_DIR.parent.resolve())}},
            {'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'started_at':stamp}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,'completed_at':stamp+1}}])

    def write_bootstrap(self, prompt, parent):
        turn = str(uuid.uuid4()); self.latest[self.target] = turn; stamp = timezone.now().timestamp()
        envelope = f'<codex_delegation>\n  <source_thread_id>{self.broker}</source_thread_id>\n  <input>{prompt}</input>\n</codex_delegation>'
        item_id = 'fco-' + str(uuid.uuid4())
        counts = {'input_tokens':100,'cached_input_tokens':40,'cache_write_input_tokens':0,
            'output_tokens':7,'reasoning_output_tokens':0,'total_tokens':107}
        records = [
            {'type':'session_meta','payload':{'session_id':self.target,'cwd':str(settings.BASE_DIR.parent.resolve())}},
            {'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'root_turn_id':turn,'started_at':stamp,
                'turn_attribution':{'turn_id':turn,'root_turn_id':turn,'turn_trigger':'app_tool_create_thread','parent_turn_id':parent}}},
            {'type':'turn_context','payload':{'turn_id':turn,'root_turn_id':turn,'model':'gpt-6-sol','effort':'high'}},
            {'type':'response_item','payload':{'type':'function_call_output','id':item_id,'namespace':'codex_app','name':'create_thread','output':envelope}},
            {'type':'event_msg','payload':{'type':'item_completed','thread_id':self.target,'turn_id':turn,
                'item':{'type':'FunctionCallOutput','id':item_id,'namespace':'codex_app','name':'create_thread','output':envelope}}},
            {'type':'token_usage_record','payload':{'thread_id':self.target,'turn_id':turn,'root_turn_id':turn,
                'response_id':'response1','usage':counts,'turn_token_usage':counts}},
            {'type':'event_msg','payload':{'type':'item_completed','thread_id':self.target,'turn_id':turn,
                'item':{'type':'AgentMessage','id':'final1','phase':'final_answer','content':[{'type':'Text','text':'CONTEXT_READY'}]}}},
            {'type':'response_item','payload':{'type':'message','id':'final1','role':'assistant','status':'completed',
                'phase':'final_answer','content':[{'type':'output_text','text':'CONTEXT_READY'}]}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,'root_turn_id':turn,
                'started_at':stamp,'completed_at':timezone.now().timestamp(),'last_agent_message':'CONTEXT_READY'}}]
        self.save(self.target, records)

    def rpc(self, client, method, params):
        self.rpc_calls.append((method, copy.deepcopy(params)))
        name = params['name']; args = params['arguments']
        if name == 'list_projects':
            self.project_lookup_calls += 1
            if self.inject_busy_after_second_lookup and self.project_lookup_calls == 2:
                from workspace.models import ModelJob
                self.busy_job = ModelJob.objects.create(charter=self.charter, owner=self.owner,
                    prompt='synthetic queued task', requested_model='gpt-6-luna',
                    requested_thinking='low', state='queued')
            result = {'projects': copy.deepcopy(self.project_rows)}
        elif name == 'read_thread':
            thread = args['threadId']
            result = {'thread': {'id':thread,'kind':'codex','hostId':'local',
                'cwd':str(settings.BASE_DIR.parent.resolve()), 'status':{'type':self.states.get(thread,'idle')}},
                'turns':[{'id':self.latest[thread],'status':'completed'}]}
        elif name == 'create_thread':
            self.write_bootstrap(args['prompt'], params['_meta']['codexTurnId'])
            if self.create_error:
                raise self.create_error
            result = {'threadId':self.target,'hostId':'local'}
        else:
            raise AssertionError('Unexpected Desktop RPC '+name)
        return {'content':[{'type':'text','text':json.dumps(result)}]}

    def count_creates(self):
        return sum(params.get('name') == 'create_thread' for _, params in self.rpc_calls)

    def test_auto_consumer_uses_saga_and_fake_desktop_rpc_once(self):
        result = maintenance()
        self.assertEqual(result['advanced'], 1)
        self.charter.refresh_from_db(); self.latch.refresh_from_db()
        self.assertEqual(self.charter.thread_id, self.target)
        self.assertEqual(self.latch.state, 'switched')
        self.assertEqual(self.count_creates(), 1)
        renewal = self.latch.renewal
        self.assertTrue(renewal.approval.evidence['automatic'])
        self.assertEqual(renewal.approval.evidence['allow_rollback'], False)
        self.assertTrue(ProjectDocumentation.objects.get(task_key=f'auto-renewal-cycle:{self.latch.request_key}').evidence['automatic'])

    def test_crash_after_binding_before_latch_finalize_recovers_without_create_or_cas_again(self):
        maintenance()
        self.latch.refresh_from_db(); self.assertEqual(self.count_creates(), 1)
        AutoRenewalLatch.objects.filter(pk=self.latch.pk).update(state='candidate_known')
        self.charter.refresh_from_db(); binding = self.charter.thread_id
        result = maintenance()
        self.latch.refresh_from_db(); self.charter.refresh_from_db()
        self.assertEqual(self.latch.state, 'switched')
        self.assertEqual(self.charter.thread_id, binding)
        self.assertEqual(self.count_creates(), 1)
        self.assertEqual(result['errors'], [])
        self.assertEqual(ProjectDocumentation.objects.filter(
            task_key=f'auto-renewal-cycle:{self.latch.request_key}').count(), 1)

    def test_revoked_policy_after_owner_intent_quarantines_and_never_recreates(self):
        from workspace.auto_renewal import _claim, _run_claimed
        claimed = _claim(self.latch.pk, timezone.now())
        self.assertIsNotNone(claimed)
        # Run until the real Desktop create RPC records its owner intent.
        with patch('workspace.renewal.commit_binding', side_effect=AssertionError('stop after create')):
            try:
                _run_claimed(claimed)
            except Exception:
                pass
        self.latch.refresh_from_db()
        self.assertEqual(self.count_creates(), 1)
        AutoRenewalPolicy.objects.filter(pk=self.auto_policy.pk).update(enabled=False)
        maintenance()
        self.assertEqual(reconcile(self.latch.pk), 'quarantined_read_only')
        self.charter.refresh_from_db()
        self.assertEqual(self.charter.thread_id, self.source_thread)
        self.assertEqual(self.count_creates(), 1)

    def test_crash_after_verify_before_cas_resumes_known_candidate_without_second_create(self):
        from workspace.auto_renewal import maintenance as run_maintenance
        with patch('workspace.renewal.commit_binding', side_effect=RuntimeError('simulated process stop')):
            result = run_maintenance()
        self.assertTrue(result['errors'])
        self.latch.refresh_from_db()
        renewal = self.latch.renewal
        self.assertEqual(renewal.state, 'verified')
        self.assertEqual(self.latch.state, 'verified')
        AutoRenewalLatch.objects.filter(pk=self.latch.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        result = run_maintenance()
        self.assertEqual(result['advanced'], 1)
        self.charter.refresh_from_db(); self.latch.refresh_from_db()
        self.assertEqual(self.charter.thread_id, self.target)
        self.assertEqual(self.latch.state, 'switched')
        self.assertEqual(self.count_creates(), 1)
        self.assertEqual(ProjectDocumentation.objects.filter(task_key=f'auto-renewal-cycle:{self.latch.request_key}').count(), 1)

    def test_unknown_candidate_after_owner_intent_is_quarantined_without_retry(self):
        self.create_error = TimeoutError('simulated lost Desktop response')
        maintenance()
        self.latch.refresh_from_db()
        self.assertEqual(self.latch.state, 'quarantined')
        self.assertFalse(self.latch.renewal.target_thread_id)
        self.assertEqual(self.count_creates(), 1)
        maintenance()
        self.assertEqual(self.count_creates(), 1)
        self.assertEqual(reconcile(self.latch.pk), 'quarantined_read_only')

    def test_busy_after_project_lookup_backs_off_then_recovers_same_request(self):
        from workspace.models import SessionRenewalEvent
        self.inject_busy_after_second_lookup = True
        maintenance()
        self.latch.refresh_from_db()
        self.assertEqual(self.latch.state, 'preintent_backoff')
        self.assertFalse(SessionRenewalEvent.objects.filter(state='owner_create_intent').exists())
        self.assertEqual(self.count_creates(), 0)
        self.busy_job.delete()
        result = maintenance()
        self.assertEqual(result['advanced'], 0)
        self.assertEqual(self.count_creates(), 0)
        AutoRenewalLatch.objects.filter(pk=self.latch.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        result = maintenance()
        self.charter.refresh_from_db(); self.latch.refresh_from_db()
        self.assertEqual(result['advanced'], 1)
        self.assertEqual(self.charter.thread_id, self.target)
        self.assertEqual(self.latch.renewal.key, self.latch.request_key)
        self.assertEqual(self.count_creates(), 1)

    def test_fresh_low_observation_after_compaction_restores_stale_latch(self):
        AutoRenewalLatch.objects.filter(pk=self.latch.pk).update(state='stale')
        self.add_observation(20, 2)
        evaluate(self.charter.pk, policy=self.capacity_policy, now=timezone.now()+timedelta(seconds=3))
        result = maintenance()
        self.charter.refresh_from_db(); self.latch.refresh_from_db()
        self.assertEqual(result['advanced'], 1)
        self.assertEqual(self.charter.thread_id, self.target)
        self.assertEqual(self.latch.state, 'switched')
        self.assertEqual(self.count_creates(), 1)

    def test_transfer_overflow_blocks_before_owner_or_model_rpc(self):
        phase = Phase.objects.create(project=self.project, title='phase', objective='bounded', order=1)
        Task.objects.bulk_create([Task(phase=phase, title=f'task {i} ' + ('x' * 180),
            description='y' * 180, assignee=self.owner, creator=self.owner, state='doing')
            for i in range(5)])
        Decision.objects.bulk_create([Decision(project=self.project, title=f'decision {i} ' + ('x' * 150),
            body='y' * 180, author=self.owner, private=False) for i in range(3)])
        for i in range(3):
            document(self.project, self.charter.key, f'task:large-{i}', 'z' * 180, {'synthetic': True})
        result = maintenance()
        self.latch.refresh_from_db()
        self.assertEqual(self.latch.state, 'blocked')
        self.assertEqual(self.latch.last_error, 'TransferBudgetError')
        self.assertEqual(result['errors'][0]['operation'], 'execute')
        self.assertEqual(self.rpc_calls, [])

    def test_owner_caller_cannot_renew_itself(self):
        self.config['caller_thread_id'] = self.source_thread
        result = maintenance()
        self.charter.refresh_from_db(); self.latch.refresh_from_db()
        self.assertEqual(result['advanced'], 0)
        self.assertEqual(self.charter.thread_id, self.source_thread)
        self.assertEqual(self.latch.state, 'ready')
        self.assertEqual(self.rpc_calls, [])
