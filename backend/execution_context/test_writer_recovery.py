import uuid
from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase
from django.contrib.auth import get_user_model
from workspace.models import Project, ModelPolicy, SessionCharter, ModelJob
from .desktop_session_bridge import BridgeError
from .models import SessionDispatch, SessionWriterRecovery
from .writer_recovery import recover_idle_writer, restore_interrupted_recoveries, _idle_snapshot


class WriterRecoveryTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username='mesuef', role='supervisor')
        project = Project.objects.create(name='test')
        policy = ModelPolicy.objects.create(category='test', title='test')
        charter = SessionCharter.objects.create(project=project, policy=policy, key='review',
            name='review', thread_id='existing-thread')
        self.job = ModelJob.objects.create(charter=charter, owner=owner, mode='workspace-write',
            prompt='test', state='waiting_delivery', requested_model='gpt-6-luna', requested_thinking='low')
        self.dispatch = SessionDispatch.objects.create(job=self.job, target_charter=charter,
            target_thread_id=charter.thread_id, dispatch_key=uuid.uuid4(), category='review',
            requested_model='gpt-6-luna', requested_thinking='low', route_reason='test')
        self.bridge = Mock()
        self.bridge.inspect_thread.return_value = 'idle'
        self.bridge.resume_idle_thread.return_value = 'idle'
        self.bridge._thread.side_effect = lambda result: result.get('thread', result)
        self.bridge.rpc.return_value = {'thread': {'id': charter.thread_id}}
        self.guard = patch('execution_context.writer_recovery._idle_snapshot', return_value={'size': 5, 'mtime_ns': 10})
        self.idle = self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_one_recovery_per_unsent_request_preserves_job_and_usage(self):
        from token_usage.models import TokenSample
        row = recover_idle_writer(self.dispatch.pk, self.bridge)
        self.assertEqual(row.state, 'released')
        self.assertEqual([c.args[0] for c in self.bridge.rpc.call_args_list], ['thread/archive', 'thread/unarchive'])
        self.assertEqual(recover_idle_writer(self.dispatch.pk, self.bridge).pk, row.pk)
        self.assertEqual(self.bridge.rpc.call_count, 2)
        self.job.refresh_from_db(); self.dispatch.refresh_from_db()
        self.assertEqual(self.job.state, 'waiting_delivery')
        self.assertEqual(self.dispatch.state, 'queued')
        self.assertEqual(TokenSample.objects.count(), 0)
        self.assertEqual(self.dispatch.turns.count(), 0)

    def test_active_or_unknown_thread_is_not_touched(self):
        self.idle.side_effect = BridgeError('active')
        self.assertIsNone(recover_idle_writer(self.dispatch.pk, self.bridge))
        self.bridge.rpc.assert_not_called()
        self.assertFalse(SessionWriterRecovery.objects.exists())

    def test_activity_change_before_archive_is_blocked_without_mutation(self):
        self.idle.side_effect = [{'size': 5}, {'size': 5}, {'size': 6}]
        row = recover_idle_writer(self.dispatch.pk, self.bridge)
        self.assertEqual(row.state, 'blocked')
        self.bridge.rpc.assert_not_called()

    def test_delivery_change_after_recovery_gate_is_not_archived(self):
        count = 0
        def idle(*args):
            nonlocal count
            count += 1
            if count == 3:
                SessionDispatch.objects.filter(pk=self.dispatch.pk).update(state='uncertain')
            return {'size': 5}
        self.idle.side_effect = idle
        row = recover_idle_writer(self.dispatch.pk, self.bridge)
        self.assertEqual(row.state, 'blocked')
        self.assertEqual(row.evidence['reason'], 'dispatch_changed_before_archive')
        self.bridge.rpc.assert_not_called()

    def test_claim_during_recovery_is_blocked_before_manifest_or_send(self):
        import io
        from django.core.management import call_command
        for state in ['archiving', 'restoring', 'uncertain']:
            SessionWriterRecovery.objects.update_or_create(dispatch=self.dispatch,
                defaults={'thread_id': self.dispatch.target_thread_id, 'state': state})
            output = io.StringIO()
            call_command('session_outbox', 'claim', stdout=output)
            self.assertEqual(output.getvalue().strip(), '{}')
            self.dispatch.refresh_from_db()
            self.assertIsNone(self.dispatch.claim_token)

    def test_known_owner_rejection_does_not_restore_or_loop(self):
        self.bridge.rpc.side_effect = BridgeError('thread already has an active writer')
        row = recover_idle_writer(self.dispatch.pk, self.bridge)
        self.assertEqual(row.state, 'blocked')
        self.assertEqual(row.evidence['reason'], 'owner_connection_required')
        recover_idle_writer(self.dispatch.pk, self.bridge)
        self.bridge.rpc.assert_called_once()

    def test_timeout_is_restored_before_any_future_delivery(self):
        self.bridge.rpc.side_effect = [BridgeError('timeout'), {'thread': {'id': 'existing-thread'}}]
        row = recover_idle_writer(self.dispatch.pk, self.bridge)
        self.assertEqual(row.state, 'released')
        self.assertEqual(self.bridge.rpc.call_count, 2)

    def test_failed_or_wrong_restore_is_quarantined_and_restart_only_unarchives(self):
        self.bridge.rpc.side_effect = [{}, {'thread': {'id': 'wrong'}}]
        row = recover_idle_writer(self.dispatch.pk, self.bridge)
        self.assertEqual(row.state, 'uncertain')
        self.bridge.reset_mock(); self.bridge.rpc.side_effect = None
        self.bridge.rpc.return_value = {'thread': {'id': 'existing-thread'}}
        restore_interrupted_recoveries(self.bridge)
        row.refresh_from_db()
        self.assertEqual(row.state, 'released')
        self.bridge.rpc.assert_called_once_with('thread/unarchive', {'threadId': 'existing-thread'})

    def test_already_sent_or_ambiguous_request_is_never_recovered(self):
        from django.utils import timezone
        self.dispatch.sent_at = timezone.now(); self.dispatch.save(update_fields=['sent_at'])
        self.assertIsNone(recover_idle_writer(self.dispatch.pk, self.bridge))
        self.dispatch.sent_at = None; self.dispatch.save(update_fields=['sent_at'])
        self.dispatch.events.create(state='uncertain', detail={})
        self.assertIsNone(recover_idle_writer(self.dispatch.pk, self.bridge))
        self.bridge.rpc.assert_not_called()

    def test_interrupted_restore_does_not_touch_an_active_thread(self):
        row = SessionWriterRecovery.objects.create(dispatch=self.dispatch,
            thread_id=self.dispatch.target_thread_id, state='archiving')
        self.bridge.inspect_thread.return_value = 'active'
        restore_interrupted_recoveries(self.bridge)
        row.refresh_from_db()
        self.assertEqual(row.state, 'uncertain')
        self.bridge.rpc.assert_not_called()

    def test_unarchive_alone_does_not_prove_writer_was_released(self):
        self.bridge.resume_idle_thread.side_effect = BridgeError('thread already has an active writer')
        row = recover_idle_writer(self.dispatch.pk, self.bridge)
        self.assertEqual(row.state, 'uncertain')
        self.assertNotIn('restored_same_thread', row.evidence)

    def test_interrupted_restore_checks_rollout_before_unarchive_even_if_rpc_idle(self):
        row = SessionWriterRecovery.objects.create(dispatch=self.dispatch,
            thread_id=self.dispatch.target_thread_id, state='archiving')
        self.bridge.inspect_thread.return_value = 'idle'
        self.idle.side_effect = BridgeError('active rollout despite idle RPC')
        restore_interrupted_recoveries(self.bridge)
        row.refresh_from_db()
        self.assertEqual(row.state, 'uncertain')
        self.bridge.rpc.assert_not_called()


class IdleSnapshotTests(SimpleTestCase):
    @patch('execution_context.desktop_session_bridge._rollout_has_active_turn', return_value=False)
    def test_restore_can_read_archived_evidence_but_refuses_duplicate_paths(self, active):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'CODEX_HOME': folder}):
            root = Path(folder)
            archived = root / 'archived_sessions' / 'rollout-thread-123.jsonl'
            archived.parent.mkdir(); archived.write_text('{}')
            bridge = Mock(); bridge.inspect_thread.return_value = 'idle'
            self.assertEqual(_idle_snapshot(bridge, 'thread-123', allow_archived=True)['size'], 2)
            live = root / 'sessions' / archived.name
            live.parent.mkdir(); live.write_text('{}')
            with self.assertRaises(BridgeError):
                _idle_snapshot(bridge, 'thread-123', allow_archived=True)

    @patch('execution_context.rollout_usage.find_rollout')
    @patch('execution_context.desktop_session_bridge._rollout_has_active_turn')
    def test_status_and_actual_rollout_are_both_required(self, active, find):
        bridge = Mock()
        for status, has_turn in [('active', False), ('unknown', False), ('idle', True)]:
            bridge.inspect_thread.return_value = status
            active.return_value = has_turn
            with self.assertRaises(BridgeError):
                _idle_snapshot(bridge, 'thread')
        find.assert_not_called()
        bridge.inspect_thread.return_value = 'idle'; active.return_value = False
        find.return_value.stat.return_value = Mock(st_size=12, st_mtime_ns=13)
        self.assertEqual(_idle_snapshot(bridge, 'thread'), {'size': 12, 'mtime_ns': 13})
