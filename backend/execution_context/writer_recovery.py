"""Bounded, auditable recovery through official owner-server RPCs only."""
from django.db import transaction
from django.utils import timezone
from .models import SessionDispatch, SessionDispatchEvent, SessionWriterRecovery


def _idle_snapshot(bridge, thread_id, *, allow_archived=False):
    from .desktop_session_bridge import BridgeError, _rollout_has_active_turn
    from .rollout_usage import find_rollout
    if bridge.inspect_thread(thread_id) not in {'idle', 'notLoaded'}:
        raise BridgeError('الجلسة نشطة أو حالتها غير معروفة؛ لا تحرير للحجز')
    if not allow_archived:
        if _rollout_has_active_turn(thread_id):
            raise BridgeError('ملف الجلسة يتضمن دورا نشطا')
        # Originally archived threads are never adopted for a new recovery.
        path = find_rollout(thread_id)
    else:
        import os
        from pathlib import Path
        root = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        matches = [path for folder in ('sessions', 'archived_sessions')
                   for path in (root / folder).glob(f'**/*-{thread_id}.jsonl')]
        if len(matches) != 1:
            raise BridgeError('تعذر تحديد دليل جلسة وحيد؛ تؤجل الاستعادة')
        path = matches[0]
        if _rollout_has_active_turn(thread_id, path=path):
            raise BridgeError('ملف الجلسة يتضمن دورا نشطا؛ تؤجل الاستعادة')
    info = path.stat()
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns}


def _finish(row, state, evidence):
    row.state = state
    row.evidence = {**row.evidence, **evidence}
    row.finished_at = timezone.now()
    row.save(update_fields=['state', 'evidence', 'finished_at'])
    SessionDispatchEvent.objects.get_or_create(dispatch=row.dispatch, state='writer_recovery_' + state,
        detail={'recovery_id': row.pk, 'thread_id': row.thread_id, **evidence})


def _restore(row, bridge):
    """Never archive again while resolving an interrupted or ambiguous restore."""
    from .desktop_session_bridge import BridgeError
    row.state = 'restoring'
    row.save(update_fields=['state'])
    try:
        if bridge.inspect_thread(row.thread_id) not in {'idle', 'notLoaded'}:
            raise BridgeError('الجلسة نشطة أو مجهولة؛ تؤجل الاستعادة دون لمسها')
        _idle_snapshot(bridge, row.thread_id, allow_archived=True)
        restored = bridge.rpc('thread/unarchive', {'threadId': row.thread_id})
        if bridge._thread(restored).get('id') != row.thread_id:
            raise BridgeError('هوية الجلسة المستعادة لا تطابق الأصل')
        if bridge.resume_idle_thread(row.thread_id) not in {'idle', 'notLoaded'}:
            raise BridgeError('لم يثبت تحرير الكاتب واستعادة جلسة خاملة')
        _idle_snapshot(bridge, row.thread_id)
    except Exception as exc:
        _finish(row, 'uncertain', {'restore_error': type(exc).__name__})
        return False
    _finish(row, 'released', {'restored_same_thread': True, 'message_sent': False})
    return True


def recover_idle_writer(dispatch_id, bridge):
    """One archive/restore attempt per unsent request, never after ambiguity."""
    from .desktop_session_bridge import BridgeError
    try:
        with transaction.atomic():
            # The write reserves SQLite before reads, as select_for_update alone
            # does not lock rows on SQLite. Existing recovery rows are immutable gates.
            SessionDispatch.objects.filter(pk=dispatch_id, state='queued').update(updated_at=timezone.now())
            dispatch = SessionDispatch.objects.select_for_update().select_related('job__owner', 'target_charter__policy').get(pk=dispatch_id)
            existing = SessionWriterRecovery.objects.filter(dispatch=dispatch).first()
            if existing:
                return existing
            job = dispatch.job
            if (dispatch.state != 'queued' or dispatch.claim_token or dispatch.sent_at or dispatch.acknowledged_at
                    or job.state != 'waiting_delivery' or job.mode != 'workspace-write'
                    or not job.owner.is_active or job.owner.role != 'supervisor'
                    or not dispatch.target_charter.policy.enabled
                    or dispatch.target_charter.thread_id != dispatch.target_thread_id
                    or dispatch.target_charter.project_id != job.charter.project_id
                    or dispatch.turns.exists() or dispatch.events.filter(state__in=['sent', 'uncertain']).exists()
                    or SessionDispatch.objects.filter(target_thread_id=dispatch.target_thread_id,
                        state__in=['claimed', 'sent', 'acknowledged', 'uncertain']).exclude(pk=dispatch.pk).exists()):
                return None
            first = _idle_snapshot(bridge, dispatch.target_thread_id)
            if _idle_snapshot(bridge, dispatch.target_thread_id) != first:
                return None
            row = SessionWriterRecovery.objects.create(dispatch=dispatch,
                thread_id=dispatch.target_thread_id, state='archiving',
                evidence={'idle_snapshot': first, 'transport': 'official-owner-rpc', 'originally_archived': False})
    except (BridgeError, OSError, ValueError):
        return None
    # An activity change before mutation is not an ambiguous archive.
    try:
        unchanged = _idle_snapshot(bridge, row.thread_id) == row.evidence['idle_snapshot']
    except (BridgeError, OSError, ValueError):
        unchanged = False
    if not unchanged:
        _finish(row, 'blocked', {'reason': 'activity_changed_before_archive'})
        return row
    dispatch.refresh_from_db()
    if (dispatch.state != 'queued' or dispatch.claim_token or dispatch.sent_at or dispatch.acknowledged_at
            or dispatch.events.filter(state__in=['sent', 'uncertain']).exists()):
        _finish(row, 'blocked', {'reason': 'dispatch_changed_before_archive'})
        return row
    try:
        bridge.rpc('thread/archive', {'threadId': row.thread_id})
    except BridgeError as exc:
        if 'already has an active writer' in str(exc):
            # This explicit owner rejection cannot have archived the thread.
            _finish(row, 'blocked', {'reason': 'owner_connection_required', 'message_sent': False})
            return row
        _restore(row, bridge)  # A timeout may have archived it; restore before any send.
        return row
    except Exception:
        _restore(row, bridge)
        return row
    _restore(row, bridge)
    return row


def restore_interrupted_recoveries(bridge):
    for row in SessionWriterRecovery.objects.filter(state__in=['archiving', 'restoring', 'uncertain']).order_by('pk')[:8]:
        _restore(row, bridge)
