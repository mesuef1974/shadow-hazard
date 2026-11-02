"""Evidence-backed task lifecycle, automation audits, and SQLite backups."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from django.conf import settings
from django.db import OperationalError, connections
from django.utils import timezone
from datetime import timedelta

from .models import AutomationIssue, BackupPolicy, BackupRun


HEARTBEAT_STALE_AFTER = timedelta(minutes=2)
DISPATCH_DELAY_AFTER = timedelta(minutes=5)
EXECUTION_DELAY_AFTER = timedelta(minutes=30)
AMBIGUOUS_AFTER = timedelta(minutes=15)
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _evidence_stage(status, evidence):
    return {"status": status if evidence or status == "not_required" else "pending", "evidence": evidence or []}


def _usage_violations(usage):
    violations = []
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in USAGE_KEYS):
        return ["usage_counters_missing_or_invalid"]
    if usage["cached_input_tokens"] > usage["input_tokens"]:
        violations.append("cached_input_exceeds_input")
    if usage["reasoning_output_tokens"] > usage["output_tokens"]:
        violations.append("reasoning_exceeds_output")
    total = usage["input_tokens"] + usage["output_tokens"]
    if "total_tokens" in usage and (type(usage["total_tokens"]) is not int or usage["total_tokens"] != total):
        violations.append("usage_total_mismatch")
    return violations


def _dispatch_evidence(job, dispatch):
    from execution_context.models import SessionDispatchEvent, SessionDispatchTurn

    violations = []
    events = list(SessionDispatchEvent.objects.filter(dispatch=dispatch).order_by("created_at", "pk"))
    turns = list(SessionDispatchTurn.objects.filter(dispatch=dispatch).order_by("created_at", "pk"))
    # Keep rejected events as history, but a later verified proof for that exact
    # turn supersedes its old counter rejection. Other turns/errors stay open.
    if any(event.state in {'evidence_rejected', 'verification_failed'} and
           not any(isinstance(event.detail, dict) and turn.turn_key == event.detail.get('turn_id')
                   and turn.created_at >= event.created_at and turn.source_evidence for turn in turns)
           for event in events):
        violations.append('execution_evidence_rejected')
    context = getattr(job, "task_context", None)
    frozen_policy = context.manifest.get("policy_snapshot", {}) if context and isinstance(context.manifest, dict) else {}
    if not isinstance(frozen_policy, dict):
        frozen_policy = {}
    if not 1 <= dispatch.attempt_no <= dispatch.max_attempts:
        violations.append("attempt_number_out_of_range")
    queued_attempts = [event.detail.get("attempt") for event in events
                       if event.state == "queued" and isinstance(event.detail, dict)
                       and type(event.detail.get("attempt")) is int]
    if queued_attempts and queued_attempts[-1] != dispatch.attempt_no:
        violations.append("attempt_event_number_mismatch")
    if dispatch.state == "completed" and not queued_attempts:
        violations.append("attempt_number_evidence_missing")
    if job.execution_thread_id and job.execution_thread_id != dispatch.target_thread_id:
        violations.append("job_thread_mismatch")
    if dispatch.state == "completed" and not dispatch.turn_key:
        violations.append("completed_dispatch_missing_turn_key")
    if dispatch.state == "completed" and not turns:
        violations.append("completed_dispatch_missing_turn_evidence")
    if dispatch.state == "completed" and not job.observed_model:
        violations.append("completed_job_missing_observed_model")
    if dispatch.state == "completed" and not job.token_usage:
        violations.append("completed_job_missing_usage")
    if dispatch.state == "completed" and len(turns) != dispatch.attempt_no:
        violations.append("completed_turn_attempt_count_mismatch")
    if len(turns) > dispatch.attempt_no:
        violations.append("turn_count_exceeds_attempt_number")
    turn_ids = set()
    sample_ids = set()
    duplicate_samples = set()
    for index, turn in enumerate(turns, start=1):
        if not turn.turn_key or turn.turn_key in turn_ids:
            violations.append("turn_key_missing_or_duplicate")
        turn_ids.add(turn.turn_key)
        if turn.target_thread_id != dispatch.target_thread_id:
            violations.append("turn_thread_mismatch")
        expected_model = frozen_policy.get("model") if index == 1 else frozen_policy.get("stuck_model")
        expected_thinking = frozen_policy.get("thinking") if index == 1 else frozen_policy.get("stuck_thinking")
        if expected_model and turn.model and turn.model != expected_model:
            violations.append("turn_model_mismatch")
        if expected_thinking and turn.thinking and turn.thinking != expected_thinking:
            violations.append("turn_thinking_mismatch")
        if index == len(turns) and turn.model and turn.model not in {dispatch.requested_model, job.observed_model}:
            violations.append("turn_model_mismatch")
        if index == len(turns) and turn.thinking and turn.thinking not in {dispatch.requested_thinking, job.requested_thinking}:
            violations.append("turn_thinking_mismatch")
        if isinstance(turn.source_sample_ids, list):
            for sample_id in turn.source_sample_ids:
                marker = str(sample_id)
                if marker in sample_ids:
                    duplicate_samples.add(marker)
                sample_ids.add(marker)
    if duplicate_samples:
        violations.append("usage_source_reused")
    if dispatch.turn_key and dispatch.turn_key not in turn_ids:
        violations.append("dispatch_turn_key_not_in_turns")
    if dispatch.state == "completed" and job.state != "completed":
        violations.append("dispatch_job_state_mismatch")
    if job.state == "completed" and dispatch.state != "completed":
        violations.append("job_dispatch_state_mismatch")
    expected_job_states = {
        "queued": {"queued", "waiting_delivery"},
        "claimed": {"running", "waiting_delivery"},
        "sent": {"running", "waiting_delivery"},
        "acknowledged": {"running", "waiting_delivery"},
        "completed": {"completed"},
        "finished_review": {"needs_review"},
        "evidence_rejected": {"needs_review"},
        "uncertain": {"uncertain", "needs_review"},
    }
    if job.state not in expected_job_states.get(dispatch.state, set()):
        violations.append("dispatch_job_state_mismatch")
    if dispatch.state == "finished_review":
        policy_event = any(event.state == "policy_mismatch" and isinstance(event.detail, dict)
                           and event.detail.get("turn_id") == dispatch.turn_key for event in events)
        if not dispatch.turn_key or dispatch.turn_key not in turn_ids or not policy_event:
            violations.append("finished_review_missing_turn_evidence")
        if not dispatch.completed_at or not job.finished_at or not job.result.strip():
            violations.append("finished_review_missing_closure_evidence")
    if dispatch.state == "evidence_rejected":
        rejected = any(event.state == "evidence_rejected" and isinstance(event.detail, dict)
                       and event.detail.get("turn_id") == dispatch.turn_key for event in events)
        if not dispatch.turn_key or not rejected or not dispatch.completed_at:
            violations.append("rejected_dispatch_missing_evidence")
    if dispatch.state == "claimed" and (not dispatch.claim_token or not dispatch.claimed_at):
        violations.append("claimed_dispatch_missing_reservation_evidence")
    if dispatch.state == "sent" and not dispatch.sent_at:
        violations.append("sent_dispatch_missing_sent_at")
    if dispatch.state == "acknowledged" and not dispatch.acknowledged_at:
        violations.append("acknowledged_dispatch_missing_arrival_time")
    if dispatch.state == "completed" and not dispatch.completed_at:
        violations.append("completed_dispatch_missing_completed_at")

    latest = next((turn for turn in reversed(turns) if turn.turn_key == dispatch.turn_key), turns[-1] if turns else None)
    turn_event = next((event for event in reversed(events)
                       if event.state in {"acknowledged", "turn_started"} and isinstance(event.detail, dict)
                       and event.detail.get("turn_id")), None)
    if latest:
        if job.observed_model and latest.model != job.observed_model:
            violations.append("observed_model_turn_mismatch")
        if latest.model and latest.model != dispatch.requested_model:
            violations.append("requested_observed_model_mismatch")
        if latest.thinking and latest.thinking != dispatch.requested_thinking:
            violations.append("requested_observed_thinking_mismatch")
        if job.token_usage and latest.usage != job.token_usage:
            violations.append("job_turn_usage_mismatch")
        if dispatch.state == "completed" and not latest.thinking:
            violations.append("observed_thinking_missing")
        violations.extend(_usage_violations(latest.usage))
        for event in events:
            detail = event.detail if isinstance(event.detail, dict) else {}
            event_turn = detail.get("turn_id") or detail.get("turn_key")
            if event_turn and event.state in {"complete-from-rollout", "stuck"} and event_turn not in turn_ids:
                violations.append("event_turn_not_recorded")
                break
        if turn_event and turn_event.detail.get("turn_id") != latest.turn_key:
            violations.append("event_recorded_turn_mismatch")
    elif turn_event:
        observed_model = turn_event.detail.get("model")
        observed_thinking = turn_event.detail.get("thinking")
        if observed_model and observed_model != dispatch.requested_model:
            violations.append("requested_observed_model_mismatch")
        if observed_thinking and observed_thinking != dispatch.requested_thinking:
            violations.append("requested_observed_thinking_mismatch")
        if job.observed_model and observed_model and job.observed_model != observed_model:
            violations.append("job_observed_model_mismatch")
    if dispatch.state == "completed" and turn_event and dispatch.turn_key != turn_event.detail.get("turn_id"):
        violations.append("event_recorded_turn_mismatch")
    if job.state == "completed" and (not job.finished_at or not job.result.strip()):
        violations.append("completed_job_missing_closure_evidence")
    return events, turns, latest, turn_event, sorted(set(violations))


def task_snapshot(job):
    """Return lifecycle stages and evidence; never infer success from a state label alone."""
    from execution_context.models import SessionDispatch
    from registry.models import UsageEvent
    from workspace.models import ProjectDocumentation

    dispatch = SessionDispatch.objects.filter(job=job).first()
    events, turns, latest_turn, turn_event, violations = _dispatch_evidence(job, dispatch) if dispatch else ([], [], None, None, [])
    context = getattr(job, "task_context", None)
    usage_event = UsageEvent.objects.filter(event_key__in=[
        f"readonly-job:{job.pk}:execution", f"delegation:{job.pk}:local-calculator",
    ]).order_by("-recorded_at").first()
    document = ProjectDocumentation.objects.filter(project=job.charter.project, task_key__in=[
        f"readonly-job:{job.pk}", f"local-job:{job.pk}",
    ]).order_by("-created_at").first()
    direct_evidence = document.evidence if document and isinstance(document.evidence, dict) else {}
    direct_turn_id = direct_evidence.get("turn_id")
    direct_thread_id = direct_evidence.get("thread_id")
    direct_thinking = direct_evidence.get("thinking")
    registration = [{"job_id": job.pk, "created_at": job.created_at.isoformat(), "submission_key": str(job.submission_key) if job.submission_key else None}]
    assignment = [{"owner_id": job.owner_id, "charter_id": job.charter_id, "charter_key": job.charter.key}]
    if context:
        assignment.append({"kind": context.kind, "manifest_digest": hashlib.sha256(json.dumps(context.manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
    dispatch_stage = []
    if dispatch:
        dispatch_stage = [{"dispatch_id": dispatch.pk, "state": dispatch.state, "target_thread_id": dispatch.target_thread_id,
                           "requested_model": dispatch.requested_model, "requested_thinking": dispatch.requested_thinking,
                           "attempt_no": dispatch.attempt_no, "max_attempts": dispatch.max_attempts,
                           "events": [{"id": e.pk, "state": e.state, "at": e.created_at.isoformat(), "detail": e.detail} for e in events],
                           "turns": [{"attempt_order": i, "turn_key": t.turn_key, "thread_id": t.target_thread_id, "model": t.model,
                                      "thinking": t.thinking, "source_sample_ids": t.source_sample_ids,
                                      "usage": t.usage, "at": t.created_at.isoformat()} for i, t in enumerate(turns, start=1)]}]
    elif job.mode == "read-only" and job.state not in {"queued", "blocked"}:
        dispatch_stage = [{"required": False, "reason": "read-only work is executed by the isolated worker; desktop outbox is not used"}]
    arrival_evidence = []
    if dispatch:
        acknowledged = dispatch.acknowledged_at or any(event.state == "acknowledged" for event in events)
        if acknowledged:
            arrival_evidence = [{"acknowledged_at": dispatch.acknowledged_at.isoformat() if dispatch.acknowledged_at else None,
                                 "target_thread_id": dispatch.target_thread_id,
                                 "turn_id": turn_event.detail.get("turn_id") if turn_event else None,
                                 "turn_keys": [t.turn_key for t in turns]}]
    elif job.execution_thread_id or direct_thread_id:
        event_evidence = usage_event.time_evidence if usage_event else ""
        has_registry_turn = bool(job.execution_thread_id and f"thread={job.execution_thread_id}" in event_evidence and "turn=" in event_evidence)
        has_documented_turn = bool(document and document.summary == job.result and direct_thread_id == job.execution_thread_id and direct_turn_id)
        if has_registry_turn or has_documented_turn:
            arrival_evidence = [{"execution_thread_id": job.execution_thread_id or direct_thread_id,
                                 "turn_id": direct_turn_id or event_evidence.partition("turn=")[2].split(";", 1)[0],
                                 "turn_evidence": event_evidence or "project database documentation"}]
    execution_evidence = []
    if job.started_at:
        execution_evidence.append({"started_at": job.started_at.isoformat()})
    if latest_turn:
        execution_evidence.append({"turn_key": latest_turn.turn_key, "model": latest_turn.model, "thinking": latest_turn.thinking})
    elif turn_event and turn_event.state == "turn_started":
        execution_evidence.append({"turn_key": turn_event.detail.get("turn_id"), "model": turn_event.detail.get("model"),
                                   "thinking": turn_event.detail.get("thinking"), "started_at": turn_event.detail.get("started_at"),
                                   "source": turn_event.detail.get("source")})
    if job.execution_thread_id and job.observed_model:
        execution_evidence.append({"thread_id": job.execution_thread_id, "observed_model": job.observed_model,
                                   "token_usage": job.token_usage, "registry_evidence": usage_event.time_evidence if usage_event else None})
    verification_evidence = []
    if dispatch:
        verification_evidence.append({"validated_turn_id": latest_turn.turn_key if latest_turn else None,
                                      "requested_model": dispatch.requested_model,
                                      "observed_model": latest_turn.model if latest_turn else turn_event.detail.get("model") if turn_event else job.observed_model,
                                      "requested_thinking": dispatch.requested_thinking,
                                      "observed_thinking": latest_turn.thinking if latest_turn else turn_event.detail.get("thinking") if turn_event else None,
                                      "evidence_source": "session_dispatch_turn" if latest_turn else turn_event.state if turn_event else None,
                                      "attempt_no": dispatch.attempt_no, "turn_count": len(turns), "violations": violations})
    elif job.observed_model == "local-arithmetic" or job.requested_model == "local-arithmetic":
        local_violations = []
        if not usage_event or usage_event.status != "completed" or usage_event.result != job.result:
            local_violations.append("local_execution_evidence_missing")
        verification_evidence.append({"execution_kind": "local_calculation", "event_key": usage_event.event_key if usage_event else None,
                                      "time_evidence": usage_event.time_evidence if usage_event else None,
                                      "violations": local_violations})
    elif job.token_usage:
        direct_violations = _usage_violations(job.token_usage)
        if job.requested_model != job.observed_model:
            direct_violations.append("requested_observed_model_mismatch")
        turn_evidence = usage_event.time_evidence if usage_event else ""
        has_documented_turn = bool(document and document.summary == job.result and direct_thread_id == job.execution_thread_id and direct_turn_id)
        if not has_documented_turn and (not job.execution_thread_id or f"thread={job.execution_thread_id}" not in turn_evidence or "turn=" not in turn_evidence):
            direct_violations.append("turn_evidence_missing")
        if usage_event and (usage_event.status != "completed" or usage_event.result != job.result):
            direct_violations.append("registry_usage_evidence_mismatch")
        documented_usage = direct_evidence.get("usage") if isinstance(direct_evidence.get("usage"), dict) else {}
        if documented_usage and any(documented_usage.get(key) != job.token_usage.get(key) for key in USAGE_KEYS):
            direct_violations.append("documented_usage_mismatch")
        observed_thinking = direct_thinking
        if job.mode == "read-only" and observed_thinking is None:
            direct_violations.append("observed_thinking_missing")
        if observed_thinking is not None and observed_thinking != job.requested_thinking:
            direct_violations.append("requested_observed_thinking_mismatch")
        verification_evidence.append({"requested_model": job.requested_model, "observed_model": job.observed_model,
                                      "requested_thinking": job.requested_thinking,
                                      "observed_thinking": observed_thinking,
                                      "turn_id_evidence": direct_turn_id or (turn_evidence or None),
                                      "violations": sorted(set(direct_violations))})
    elif usage_event:
        event_violations = []
        if usage_event.status != "completed" or usage_event.result != job.result:
            event_violations.append("registry_usage_evidence_mismatch")
        verification_evidence.append({"event_key": usage_event.event_key, "status": usage_event.status,
                                      "time_evidence": usage_event.time_evidence, "method": usage_event.method,
                                      "result_recorded": bool(usage_event.result), "violations": event_violations})
    verified = bool(job.state == "completed" and job.finished_at and job.result.strip() and verification_evidence and
                    not violations and not verification_evidence[-1].get("violations"))
    closure = [{"state": job.state, "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "outcome_note": job.outcome_note}] if job.state in {"completed", "blocked", "needs_review", "uncertain"} else []
    all_violations = sorted(set(violations + (verification_evidence[-1].get("violations", []) if verification_evidence else [])))
    return {
        "job_id": job.pk, "state": job.state,
        "requested": {"model": job.requested_model, "thinking": job.requested_thinking, "mode": job.mode},
        "observed": {"model": latest_turn.model if latest_turn else turn_event.detail.get("model") if turn_event else job.observed_model or None,
                     "thread_id": dispatch.target_thread_id if dispatch else job.execution_thread_id or direct_thread_id,
                     "turn_id": latest_turn.turn_key if latest_turn else turn_event.detail.get("turn_id") if turn_event else direct_turn_id,
                     "thinking": latest_turn.thinking if latest_turn else turn_event.detail.get("thinking") if turn_event else direct_thinking,
                     "evidence_source": "session_dispatch_turn" if latest_turn else turn_event.state if turn_event else "project_documentation" if direct_turn_id else None},
        "stages": {
            "registration": _evidence_stage("recorded", registration),
            "assignment": _evidence_stage("recorded", assignment),
            "dispatch": _evidence_stage("recorded" if dispatch else "not_required" if dispatch_stage else "pending", dispatch_stage),
            "arrival": _evidence_stage("not_required" if job.requested_model == "local-arithmetic" and usage_event else "confirmed", arrival_evidence),
            "execution": _evidence_stage("confirmed", execution_evidence),
            "verification": _evidence_stage("verified" if verified else "failed" if all_violations else "pending", verification_evidence),
            "closure": _evidence_stage("closed", closure),
        },
        "violations": all_violations,
        "success_verified": verified,
        "usage_source": "session_dispatch_turn" if dispatch else "model_job_direct" if job.token_usage else None,
        "usage_is_added_to_token_samples": False,
        "codex_queue_controls_model": False,
    }


def _issue_fingerprint(code, job_id=None, source_key=""):
    raw = f"{code}:{job_id or 'global'}:{source_key}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _record_issue(*, code, title, evidence, now, job=None, severity="warning"):
    fingerprint = _issue_fingerprint(code, job.pk if job else None, str(evidence.get("source_key", "")))
    row, created = AutomationIssue.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={"job": job, "code": code, "severity": severity, "title": title,
                  "evidence": evidence, "state": "open", "last_seen": now},
    )
    if not created:
        AutomationIssue.objects.filter(pk=row.pk).update(job=job, code=code, severity=severity, title=title,
            evidence=evidence, state="open", last_seen=now, resolved_at=None)
    return row.pk, created


def _result_issues(job, dispatch, now):
    if dispatch:
        _, turns, _, _, violations = _dispatch_evidence(job, dispatch)
        from execution_context.models import SessionWriterRecovery
        recovery = SessionWriterRecovery.objects.filter(dispatch=dispatch).first()
        if recovery and recovery.state in {'blocked', 'uncertain'}:
            violations.append('writer_recovery_' + recovery.state)
        return [(code, "مخالفة في دليل الدور أو الاستهلاك", {"dispatch_id": dispatch.pk, "state": dispatch.state,
                 "attempt_no": dispatch.attempt_no, "turn_count": len(turns), "source_key": code}) for code in violations]
    if job.state == "completed" and job.finished_at and job.result.strip():
        snapshot = task_snapshot(job)
        codes = snapshot["violations"] or ([] if snapshot["success_verified"] else ["completion_not_verified"])
        return [(code, "دليل التنفيذ أو الإغلاق غير مكتمل", {"job_id": job.pk, "source_key": code,
                 "observed_model": job.observed_model or None, "thread_id": job.execution_thread_id or None}) for code in codes]
    return []


def audit_automation(now=None):
    """Record current automation blockers idempotently; never reclaims or resends work."""
    from execution_context.models import SessionDispatch
    from workspace.models import DispatcherHeartbeat, ModelJob

    now = now or timezone.now()
    seen = set()
    created = 0
    heartbeat_rows = DispatcherHeartbeat.objects.all()
    for heartbeat in heartbeat_rows:
        if heartbeat.state=='failed' or heartbeat.last_error:
            pk,new=_record_issue(code='dispatcher_failure',title='عطل مسجل في عامل الأتمتة',
                evidence={'heartbeat_id':heartbeat.pk,'state':heartbeat.state,'has_error':bool(heartbeat.last_error),
                    'source_key':heartbeat.key},now=now,severity='critical')
            seen.add(pk);created+=new
        if heartbeat.state=='running' and heartbeat.updated_at < now - HEARTBEAT_STALE_AFTER:
            pk, new = _record_issue(code="stale_dispatcher_heartbeat", title="نبض موزّع المهام متأخر",
                evidence={"heartbeat_id": heartbeat.pk, "updated_at": heartbeat.updated_at.isoformat(), "pid": heartbeat.pid,
                          "source_key": heartbeat.key}, now=now, severity="critical")
            seen.add(pk); created += new
    for job in ModelJob.objects.select_related("owner", "charter").order_by("pk"):
        dispatch = SessionDispatch.objects.filter(job=job).first()
        if job.state == "uncertain" or (dispatch and dispatch.state == "uncertain"):
            pk, new = _record_issue(code="ambiguous_reservation", title="حجز أو نتيجة غير مؤكدة؛ تحتاج مراجعة",
                evidence={"job_id": job.pk, "job_state": job.state, "dispatch_id": dispatch.pk if dispatch else None,
                          "dispatch_state": dispatch.state if dispatch else None, "source_key": "reservation"},
                now=now, job=job, severity="critical")
            seen.add(pk); created += new
        if job.state == "waiting_delivery" and job.created_at < now - DISPATCH_DELAY_AFTER and (not dispatch or dispatch.state == "queued"):
            pk, new = _record_issue(code="dispatch_delay", title="المهمة تنتظر الإرسال مدة أطول من الحد",
                evidence={"job_id": job.pk, "state": job.state, "created_at": job.created_at.isoformat(),
                          "dispatch_state": dispatch.state if dispatch else None, "source_key": "delivery"}, now=now, job=job)
            seen.add(pk); created += new
        if job.state == "running" and job.started_at and job.started_at < now - EXECUTION_DELAY_AFTER:
            pk, new = _record_issue(code="execution_delay", title="التنفيذ تجاوز المهلة التشغيلية",
                evidence={"job_id": job.pk, "started_at": job.started_at.isoformat(),
                          "dispatch_id": dispatch.pk if dispatch else None, "source_key": "execution"}, now=now, job=job)
            seen.add(pk); created += new
        if dispatch and dispatch.state in {"claimed", "sent", "acknowledged"}:
            turn_started = dispatch.events.filter(state="turn_started").order_by("created_at").first()
            reservation_at = turn_started.created_at if turn_started else dispatch.updated_at
            threshold = EXECUTION_DELAY_AFTER if turn_started else AMBIGUOUS_AFTER
            if reservation_at < now - threshold:
                code = "execution_delay" if turn_started else "stale_dispatch_reservation"
                title = "التنفيذ تجاوز المهلة بعد بدء الدور" if turn_started else "حجز إرسال قديم؛ لا تعاد إرساله آليًا"
                pk, new = _record_issue(code=code, title=title,
                    evidence={"job_id": job.pk, "dispatch_id": dispatch.pk, "dispatch_state": dispatch.state,
                              "observed_at": reservation_at.isoformat(), "turn_started": bool(turn_started),
                              "claim_token_present": bool(dispatch.claim_token),
                              "source_key": "execution" if turn_started else "dispatch-reservation"},
                    now=now, job=job, severity="critical")
                seen.add(pk); created += new
        if job.state == "completed" and (not job.finished_at or not job.result.strip()):
            pk, new = _record_issue(code="completed_without_evidence", title="حالة مكتملة بلا دليل إغلاق",
                evidence={"job_id": job.pk, "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                          "has_result": bool(job.result.strip()), "source_key": "closure"}, now=now, job=job, severity="critical")
            seen.add(pk); created += new
        if dispatch and job.state == "completed" and dispatch.state != "completed":
            pk, new = _record_issue(code="dispatch_state_violation", title="حالة المهمة لا تطابق حالة الإرسال",
                evidence={"job_id": job.pk, "job_state": job.state, "dispatch_id": dispatch.pk,
                          "dispatch_state": dispatch.state, "source_key": "dispatch-job-state"}, now=now, job=job, severity="critical")
            seen.add(pk); created += new
        for code, title, evidence in _result_issues(job, dispatch, now):
            pk, new = _record_issue(code=code, title=title, evidence=evidence, now=now, job=job, severity="critical")
            seen.add(pk); created += new
    from execution_context.models import SessionDispatchTurn
    source_uses = {}
    for turn in SessionDispatchTurn.objects.select_related("dispatch", "dispatch__job").all():
        for sample_id in turn.source_sample_ids if isinstance(turn.source_sample_ids, list) else []:
            key = (turn.target_thread_id, str(sample_id))
            source_uses.setdefault(key, []).append(turn)
    conflict_by_job = {}
    for (thread_id, sample_id), uses in source_uses.items():
        if len({(turn.dispatch_id, turn.turn_key) for turn in uses}) < 2:
            continue
        for turn in uses:
            bucket = conflict_by_job.setdefault(turn.dispatch.job_id, {"job": turn.dispatch.job, "duplicates": []})
            bucket["duplicates"].append({"thread_id": thread_id, "source_sample_id": sample_id,
                                          "turn_key": turn.turn_key, "dispatch_id": turn.dispatch_id})
    for bucket in conflict_by_job.values():
        evidence = {"duplicates": bucket["duplicates"], "source_key": "usage_source_reused"}
        pk, new = _record_issue(code="usage_source_reused", title="مصدر استهلاك واحد مرتبط بأكثر من دور",
            evidence=evidence, now=now, job=bucket["job"], severity="critical")
        seen.add(pk); created += new
    resolved = AutomationIssue.objects.filter(state="open").exclude(pk__in=seen).update(state="resolved", resolved_at=now)
    return {"created": created, "active": len(seen), "resolved": resolved,
            "issues": list(AutomationIssue.objects.filter(pk__in=seen).order_by("severity", "pk").values(
                "id", "job_id", "code", "severity", "title", "evidence"))}


def _backup_root():
    return Path(getattr(settings, "OPERATIONS_BACKUP_DIR", settings.BASE_DIR / ".local" / "backups")).resolve()


def _restore_root():
    return Path(getattr(settings, "OPERATIONS_RESTORE_DIR", settings.BASE_DIR / ".local" / "restores")).resolve()


def _sqlite_source_path(using="default"):
    connection = connections[using]
    if connection.vendor != "sqlite":
        raise ValueError("sqlite_only")
    raw = connection.settings_dict["NAME"]
    if raw == ":memory:" or str(raw).startswith("file:"):
        raise ValueError("database_file_required")
    path = Path(raw).resolve()
    if not path.is_file():
        raise ValueError("database_file_missing")
    return path


def _integrity(path):
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_local_path(root, relative):
    target = (root.resolve().parent / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("path_outside_managed_directory")
    return target


def _prune_backups(current_run, policy):
    """Rotate successful backups only within the dedicated backup directory."""
    root = _backup_root().resolve()
    successful = list(BackupRun.objects.filter(state="success", relative_path__startswith="backups/")
                      .order_by("-finished_at", "-pk"))
    keep_ids = {current_run.pk, *(row.pk for row in successful[:max(0, policy.retained_successful - 1)])}
    if len([row for row in successful if row.pk in keep_ids]) < min(policy.retained_successful, len(successful)):
        return 0
    removed = 0
    for row in successful:
        if row.pk in keep_ids:
            continue
        try:
            path = _safe_local_path(root, row.relative_path)
            if path.parent.resolve() != root or path.suffix != ".sqlite3":
                continue
            if path.is_file() and _hash(path) == row.sha256:
                path.unlink()
                removed += 1
        except (OSError, ValueError):
            continue
    return removed


def backup_database(*, kind="manual", using="default", register=True):
    """Create, integrity-check, hash, register, and safely rotate an SQLite backup."""
    now = timezone.now()
    run = None
    root = _backup_root()
    root.mkdir(parents=True, exist_ok=True)
    temp_path = None
    final_path = None
    try:
        source_path = _sqlite_source_path(using)
        if source_path == root or source_path.is_relative_to(root):
            raise ValueError("source_inside_backup_directory")
        name = f"shadow-hazard-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}.sqlite3"
        final_path = (root / name).resolve()
        if final_path.parent != root.resolve():
            raise ValueError("invalid_backup_path")
        fd, temp_name = tempfile.mkstemp(prefix=".backup-", suffix=".tmp", dir=root)
        os.close(fd)
        temp_path = Path(temp_name)
        source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=20)
        destination = sqlite3.connect(temp_path, timeout=20)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        integrity = _integrity(temp_path)
        if integrity != "ok":
            raise ValueError("integrity_check_failed")
        digest = _hash(temp_path)
        size = temp_path.stat().st_size
        os.replace(temp_path, final_path)
        temp_path = None
        if register:
            run = BackupRun.objects.create(kind=kind, state="success", relative_path=f"backups/{name}", sha256=digest,
                size_bytes=size, integrity_result=integrity, finished_at=timezone.now(), details={"source": "sqlite_backup_api"})
        pruned = 0
        if register and run:
            policy = BackupPolicy.objects.filter(key="default", enabled=True).first()
            if policy:
                pruned = _prune_backups(run, policy)
        return {"run": run, "path": final_path, "sha256": digest, "size_bytes": size,
                "integrity": integrity, "verified": True, "pruned": pruned}
    except Exception as exc:
        if register and run is None:
            try:
                BackupRun.objects.create(kind=kind, state="failed", error_code=type(exc).__name__, finished_at=timezone.now())
            except OperationalError:
                pass
        raise
    finally:
        if temp_path and temp_path.is_file() and temp_path.parent.resolve() == root.resolve():
            temp_path.unlink(missing_ok=True)


def run_due_backup(now=None):
    now = now or timezone.now()
    policy = BackupPolicy.objects.filter(key="default", enabled=True).first()
    if not policy:
        return {"due": False, "reason": "policy_disabled_or_missing"}
    latest = BackupRun.objects.filter(state="success", kind__in=["scheduled", "manual", "pre_migration"]).order_by("-finished_at").first()
    if latest and latest.finished_at and latest.finished_at > now - timedelta(hours=policy.interval_hours):
        return {"due": False, "reason": "interval_not_elapsed", "last_success_id": latest.pk}
    result = backup_database(kind="scheduled")
    return {"due": True, **result}


def restore_backup_to_separate_database(run_id):
    """Restore a verified backup to backend/.local/restores only; never alter DATABASES."""
    source_run = BackupRun.objects.get(pk=run_id, state="success", kind__in=["manual", "scheduled", "pre_migration"])
    backup_root = _backup_root().resolve()
    source_path = _safe_local_path(backup_root, source_run.relative_path)
    if source_path.parent.resolve() != backup_root or not source_path.is_file() or _hash(source_path) != source_run.sha256:
        raise ValueError("backup_hash_or_path_invalid")
    if _integrity(source_path) != "ok":
        raise ValueError("backup_integrity_invalid")
    restore_root = _restore_root()
    restore_root.mkdir(parents=True, exist_ok=True)
    name = f"restore-{source_run.pk}-{uuid.uuid4().hex[:12]}.sqlite3"
    destination_path = (restore_root / name).resolve()
    if destination_path.parent != restore_root.resolve() or destination_path == _sqlite_source_path():
        raise ValueError("restore_destination_invalid")
    run = BackupRun.objects.create(kind="restore", state="running", source=source_run)
    try:
        source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=20)
        destination = sqlite3.connect(destination_path, timeout=20)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        integrity = _integrity(destination_path)
        digest = _hash(destination_path)
        if integrity != "ok":
            raise ValueError("restored_integrity_check_failed")
        run.state = "success"
        run.relative_path = f"restores/{name}"
        run.sha256 = digest
        run.size_bytes = destination_path.stat().st_size
        run.integrity_result = integrity
        run.finished_at = timezone.now()
        run.details = {"source_run_id": source_run.pk, "database_replaced": False}
        run.save(update_fields=["state", "relative_path", "sha256", "size_bytes", "integrity_result", "finished_at", "details"])
        return {"run": run, "path": destination_path, "sha256": digest, "integrity": integrity,
                "verified": True, "database_replaced": False}
    except Exception as exc:
        run.state = "failed"
        run.error_code = str(exc)[:40] if str(exc).isidentifier() else type(exc).__name__
        run.finished_at = timezone.now()
        run.save(update_fields=["state", "error_code", "finished_at"])
        if destination_path.is_file() and destination_path.parent.resolve() == restore_root.resolve():
            destination_path.unlink(missing_ok=True)
        raise
