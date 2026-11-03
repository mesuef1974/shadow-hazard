import sqlite3
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from execution_context.models import (
    SessionDispatch, SessionDispatchEvent, SessionDispatchTurn, SessionRoutingPolicy, TaskContext,
)
from operations.models import AutomationIssue, BackupPolicy, BackupRun
from operations.services import audit_automation, restore_backup_to_separate_database, run_due_backup, task_snapshot
from workspace.models import DispatcherHeartbeat, ModelJob, ModelPolicy, Project, SessionCharter
from .services import backup_database, _sqlite_source_path


class OperationsTests(TransactionTestCase):
    def setUp(self):
        self.project = Project.objects.create(name="operations-test")
        BackupPolicy.objects.get_or_create(key="default")
        self.owner = get_user_model().objects.create_user(username="mesuef", role="supervisor")
        policy = ModelPolicy.objects.create(category="backend", title="باك إند")
        self.charter = SessionCharter.objects.create(key="backend", name="باك إند", project=self.project,
            policy=policy, scope="test", rules="test")

    def sqlite_backup_source(self, path):
        # The backup transport is SQLite-only; its registry may use either backend.
        source = sqlite3.connect(path)
        try:
            source.execute("CREATE TABLE workspace_project (name TEXT NOT NULL)")
            source.execute("INSERT INTO workspace_project (name) VALUES (?)", ("backup-content-proof",))
            source.commit()
        finally:
            source.close()
        return path

    def make_job(self, *, state="completed"):
        job = ModelJob.objects.create(charter=self.charter, owner=self.owner, prompt="test",
            requested_model="gpt-6-luna", requested_thinking="low", mode="workspace-write",
            state=state, started_at=timezone.now(), finished_at=timezone.now() if state == "completed" else None,
            result="done" if state == "completed" else "")
        TaskContext.objects.create(job=job, kind="code", skills=[], plugins=[], instructions="", charter="",
            manifest={"policy_snapshot": {"model": "gpt-6-luna", "thinking": "low",
                                           "stuck_model": "gpt-6.1-sol", "stuck_thinking": "medium"}})
        return job

    def make_dispatch(self, job, *, state="completed", attempt=1):
        route, _ = SessionRoutingPolicy.objects.get_or_create(category="code", defaults={"model": "gpt-6-luna", "thinking": "low"})
        dispatch = SessionDispatch.objects.create(job=job, dispatch_key=uuid.uuid4(), target_charter=self.charter,
            target_thread_id="thread-ops", category="code", routing_policy=route, attempt_no=attempt,
            max_attempts=2, requested_model="gpt-6.1-sol" if attempt == 2 else "gpt-6-luna",
            requested_thinking="medium" if attempt == 2 else "low", route_reason="test", state=state,
            turn_key=("turn-2" if attempt == 2 else "turn-1") if state == "completed" else "",
            acknowledged_at=timezone.now() if state in {"completed", "acknowledged"} else None,
            completed_at=timezone.now() if state == "completed" else None)
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="queued", detail={"attempt": attempt})
        job.execution_thread_id = dispatch.target_thread_id
        job.requested_model = dispatch.requested_model
        job.requested_thinking = dispatch.requested_thinking
        job.observed_model = dispatch.requested_model if state == "completed" else ""
        job.save(update_fields=["execution_thread_id", "requested_model", "requested_thinking", "observed_model"])
        return dispatch

    @staticmethod
    def usage():
        return {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10,
                "reasoning_output_tokens": 2, "total_tokens": 110}

    def add_turn(self, dispatch, turn_id, *, model="gpt-6-luna", thinking="low", source_ids=None, usage=None):
        return SessionDispatchTurn.objects.create(dispatch=dispatch, target_thread_id=dispatch.target_thread_id,
            turn_key=turn_id, model=model, thinking=thinking, source_sample_ids=source_ids or [],
            usage=usage or self.usage(), source_evidence={"rollout": "verified"})

    def test_terminal_policy_review_is_not_an_active_execution_or_success(self):
        job = self.make_job(state="needs_review")
        dispatch = self.make_dispatch(job, state="finished_review")
        dispatch.turn_key = "reviewed-turn"
        dispatch.completed_at = timezone.now()
        dispatch.save(update_fields=["turn_key", "completed_at"])
        job.token_usage = self.usage()
        job.observed_model = "gpt-6.1-sol"
        job.finished_at = timezone.now()
        job.result = 'reviewed result'
        job.save(update_fields=["token_usage", "observed_model", "finished_at", "result"])
        self.add_turn(dispatch, "reviewed-turn", model="gpt-6.1-sol")
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="policy_mismatch", detail={"turn_id": "reviewed-turn"})
        snapshot = task_snapshot(job)
        self.assertNotIn("dispatch_job_state_mismatch", snapshot["violations"])
        self.assertNotIn("finished_review_missing_closure_evidence", snapshot["violations"])
        self.assertIn("requested_observed_model_mismatch", snapshot["violations"])
        self.assertFalse(snapshot["success_verified"])
        audit_automation(timezone.now() + timedelta(hours=2))
        self.assertFalse(AutomationIssue.objects.filter(code="execution_delay", state="open").exists())

    def test_rejected_terminal_evidence_stays_visible_without_counting_usage(self):
        from token_usage.models import TokenSample
        job = self.make_job(state="needs_review")
        dispatch = self.make_dispatch(job, state="evidence_rejected")
        dispatch.turn_key = "rejected-turn"
        dispatch.completed_at = timezone.now()
        dispatch.save(update_fields=["turn_key", "completed_at"])
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="evidence_rejected", detail={"turn_id": "rejected-turn"})
        snapshot = task_snapshot(job)
        self.assertIn("execution_evidence_rejected", snapshot["violations"])
        self.assertNotIn("dispatch_job_state_mismatch", snapshot["violations"])
        self.assertFalse(snapshot["success_verified"])
        self.assertEqual(TokenSample.objects.count(), 0)
        dispatch.turn_key = "unbound-turn"
        dispatch.save(update_fields=["turn_key"])
        self.assertIn("rejected_dispatch_missing_evidence", task_snapshot(job)["violations"])

    def test_later_exact_turn_proof_supersedes_rejection_without_erasing_history(self):
        job = self.make_job()
        dispatch = self.make_dispatch(job)
        job.token_usage = self.usage()
        job.save(update_fields=["token_usage"])
        rejected = SessionDispatchEvent.objects.create(dispatch=dispatch, state="evidence_rejected", detail={"turn_id": "turn-1"})
        self.assertIn("execution_evidence_rejected", task_snapshot(job)["violations"])
        self.add_turn(dispatch, "turn-1")
        self.assertNotIn("execution_evidence_rejected", task_snapshot(job)["violations"])
        self.assertTrue(SessionDispatchEvent.objects.filter(pk=rejected.pk).exists())
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="verification_failed", detail={"turn_id": "other-turn"})
        self.assertIn("execution_evidence_rejected", task_snapshot(job)["violations"])

    def test_task_snapshot_requires_consistent_turn_and_closure_evidence(self):
        job = self.make_job()
        dispatch = self.make_dispatch(job)
        job.token_usage = self.usage()
        job.save(update_fields=["token_usage"])
        self.add_turn(dispatch, "turn-1")
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="acknowledged", detail={"turn_id": "turn-1"})
        snapshot = task_snapshot(job)
        self.assertTrue(snapshot["success_verified"])
        self.assertEqual(snapshot["observed"]["turn_id"], "turn-1")
        self.assertFalse(snapshot["usage_is_added_to_token_samples"])
        self.assertFalse(snapshot["codex_queue_controls_model"])

    def test_history_reconciliation_is_bounded_idempotent_and_keeps_acceptance_pending(self):
        from operations.reconciliation import reconcile_transport_history
        from token_usage.models import TokenSample
        job = self.make_job(state='needs_review')
        dispatch = self.make_dispatch(job, state='acknowledged')
        dispatch.turn_key = 'historic-turn'
        dispatch.save(update_fields=['turn_key'])
        job.token_usage = self.usage()
        job.observed_model = 'gpt-6.1-sol'
        job.finished_at = timezone.now()
        job.result = 'historic result'
        job.save(update_fields=['token_usage', 'observed_model', 'finished_at', 'result'])
        self.add_turn(dispatch, 'historic-turn', model=job.observed_model)
        self.assertEqual(reconcile_transport_history([job.pk])['corrected'], [])
        SessionDispatchEvent.objects.create(dispatch=dispatch, state='policy_mismatch', detail={'turn_id': 'historic-turn'})
        self.assertEqual(reconcile_transport_history([job.pk])['corrected'], [job.pk])
        self.assertEqual(reconcile_transport_history([job.pk])['corrected'], [])
        dispatch.refresh_from_db(); job.refresh_from_db()
        self.assertEqual(dispatch.state, 'finished_review')
        self.assertEqual(dispatch.completed_at, job.finished_at)
        self.assertEqual(job.state, 'needs_review')
        self.assertEqual(job.token_usage, self.usage())
        self.assertEqual(TokenSample.objects.count(), 0)
        self.assertEqual(dispatch.events.filter(state='transport_history_reconciled').count(), 1)
        with self.assertRaises(ValueError):
            reconcile_transport_history([])

    def test_queue_acknowledgement_exposes_actual_model_and_thinking_mismatch(self):
        job = self.make_job(state="waiting_delivery")
        dispatch = self.make_dispatch(job, state="acknowledged")
        dispatch.requested_model = "gpt-6.1-sol"
        dispatch.requested_thinking = "medium"
        dispatch.save(update_fields=["requested_model", "requested_thinking"])
        job.requested_model = dispatch.requested_model
        job.requested_thinking = dispatch.requested_thinking
        job.observed_model = "gpt-6-luna"
        job.save(update_fields=["requested_model", "requested_thinking", "observed_model"])
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="acknowledged", detail={
            "turn_id": "turn-active", "model": "gpt-6-luna", "thinking": "low",
            "source": "incoming user task marker and turn_context",
        })
        snapshot = task_snapshot(job)
        self.assertEqual(snapshot["observed"]["turn_id"], "turn-active")
        self.assertEqual(snapshot["observed"]["model"], "gpt-6-luna")
        self.assertEqual(snapshot["observed"]["thinking"], "low")
        self.assertIn("requested_observed_model_mismatch", snapshot["violations"])
        self.assertIn("requested_observed_thinking_mismatch", snapshot["violations"])
        self.assertFalse(snapshot["codex_queue_controls_model"])
        self.assertFalse(snapshot["success_verified"])

    def test_attempt_order_and_duplicate_usage_source_are_reported(self):
        job = self.make_job()
        dispatch = self.make_dispatch(job, attempt=2)
        job.token_usage = self.usage()
        job.save(update_fields=["token_usage"])
        self.add_turn(dispatch, "turn-1", source_ids=[7])
        self.add_turn(dispatch, "turn-2", model="gpt-6.1-sol", thinking="medium", source_ids=[7])
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="acknowledged", detail={"turn_id": "turn-1"})
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="acknowledged", detail={"turn_id": "turn-2"})
        snapshot = task_snapshot(job)
        self.assertIn("usage_source_reused", snapshot["violations"])
        self.assertFalse(snapshot["success_verified"])
        self.assertEqual(snapshot["observed"]["turn_id"], "turn-2")
        from token_usage.models import TokenSample
        self.assertEqual(TokenSample.objects.count(), 0)

    def test_direct_readonly_job_is_not_verified_without_persisted_turn_and_thinking(self):
        job = self.make_job()
        job.mode = "read-only"
        job.execution_thread_id = "thread-direct"
        job.observed_model = job.requested_model
        job.token_usage = self.usage()
        job.save(update_fields=["mode", "execution_thread_id", "observed_model", "token_usage"])
        snapshot = task_snapshot(job)
        self.assertFalse(snapshot["success_verified"])
        self.assertIsNone(snapshot["observed"]["thinking"])
        self.assertIn("observed_thinking_missing", snapshot["violations"])
        self.assertIn("turn_evidence_missing", snapshot["violations"])

    def test_audit_flags_completed_state_without_execution_evidence(self):
        job = self.make_job()
        snapshot = task_snapshot(job)
        self.assertFalse(snapshot["success_verified"])
        audit_automation()
        self.assertTrue(AutomationIssue.objects.filter(job=job, code="completion_not_verified", state="open").exists())

    def test_audit_is_idempotent_and_preserves_uncertain_reservation(self):
        job = self.make_job(state="uncertain")
        dispatch = self.make_dispatch(job, state="uncertain")
        dispatch.claim_token = uuid.uuid4()
        dispatch.save(update_fields=["claim_token"])
        now = timezone.now()
        first = audit_automation(now)
        second = audit_automation(now + timedelta(seconds=1))
        self.assertEqual(first["created"], 1, first["issues"])
        self.assertEqual(second["created"], 0)
        self.assertEqual(AutomationIssue.objects.filter(code="ambiguous_reservation", state="open").count(), 1)
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.state, "uncertain")
        self.assertIsNotNone(dispatch.claim_token)

    def test_audit_resolves_stale_heartbeat_only_after_state_changes(self):
        heartbeat = DispatcherHeartbeat.objects.create(key="local", state="running")
        stale = timezone.now() - timedelta(minutes=5)
        DispatcherHeartbeat.objects.filter(pk=heartbeat.pk).update(updated_at=stale)
        first = audit_automation()
        issue = AutomationIssue.objects.get(code="stale_dispatcher_heartbeat")
        self.assertEqual(first["created"], 1)
        DispatcherHeartbeat.objects.filter(pk=heartbeat.pk).update(state="stopped")
        audit_automation()
        issue.refresh_from_db()
        self.assertEqual(issue.state, "resolved")

    def test_backup_hash_integrity_and_real_separate_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with override_settings(OPERATIONS_BACKUP_DIR=root / "backups", OPERATIONS_RESTORE_DIR=root / "restores"):
                from django.db import connections
                source_path = self.sqlite_backup_source(root / "source.sqlite3")
                with patch("operations.services._sqlite_source_path", return_value=source_path):
                    result = backup_database()
                self.assertTrue(result["verified"])
                self.assertEqual(result["integrity"], "ok")
                self.assertEqual(result["run"].sha256, result["sha256"])
                before = str(connections["default"].settings_dict["NAME"])
                with patch("operations.services._sqlite_source_path", return_value=source_path):
                    restored = restore_backup_to_separate_database(result["run"].pk)
                self.assertTrue(restored["verified"])
                self.assertFalse(restored["database_replaced"])
                check = sqlite3.connect(restored["path"])
                try:
                    self.assertEqual(check.execute(
                        "SELECT COUNT(*) FROM workspace_project WHERE name='backup-content-proof'").fetchone()[0], 1)
                finally:
                    check.close()
                after = str(connections["default"].settings_dict["NAME"])
                self.assertEqual(before, after)

    def test_retention_removes_only_verified_old_backups_inside_backup_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with override_settings(OPERATIONS_BACKUP_DIR=root / "backups", OPERATIONS_RESTORE_DIR=root / "restores"):
                source_path = self.sqlite_backup_source(root / "source.sqlite3")
                with patch("operations.services._sqlite_source_path", return_value=source_path):
                    first = backup_database()
                policy = BackupPolicy.objects.get(key="default")
                policy.retained_successful = 1
                policy.save(update_fields=["retained_successful", "updated_at"])
                outside = root / "restores" / "must-remain.sqlite3"
                outside.parent.mkdir(parents=True, exist_ok=True)
                outside.write_bytes(b"preserve")
                import hashlib
                BackupRun.objects.create(kind="manual", state="success", relative_path="backups/../restores/must-remain.sqlite3",
                    sha256=hashlib.sha256(outside.read_bytes()).hexdigest(), size_bytes=outside.stat().st_size,
                    integrity_result="ok", finished_at=timezone.now())
                with patch("operations.services._sqlite_source_path", return_value=source_path):
                    second = backup_database()
                self.assertEqual(second["pruned"], 1)
                self.assertFalse(first["path"].exists())
                self.assertTrue(second["path"].exists())
                self.assertTrue(outside.exists())

    def test_due_backup_runs_once_per_policy_interval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with override_settings(OPERATIONS_BACKUP_DIR=root / "backups", OPERATIONS_RESTORE_DIR=root / "restores"):
                source_path = self.sqlite_backup_source(root / "source.sqlite3")
                with patch("operations.services._sqlite_source_path", return_value=source_path):
                    first = run_due_backup()
                    second = run_due_backup(now=timezone.now() + timedelta(minutes=1))
                self.assertTrue(first["due"])
                self.assertEqual(first["run"].kind, "scheduled")
                self.assertFalse(second["due"])

    def test_sqlite_backup_rejects_other_database_engines(self):
        with patch('operations.services.connections', {'default': SimpleNamespace(vendor='postgresql')}):
            with self.assertRaisesMessage(ValueError, 'sqlite_only'):
                _sqlite_source_path()
