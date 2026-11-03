"""Atomic startup recovery, under the dispatcher's existing OS worker lock."""
import time
from django.db import connection, transaction, OperationalError
from django.utils import timezone
from .models import DispatcherRun, DispatcherHeartbeat, ModelJob
from execution_context.models import SessionDispatch, SessionDispatchEvent


@transaction.atomic
def recover_state(pid):
    now = timezone.now()
    if connection.vendor == 'sqlite':
        # Reserve the SQLite writer before selecting claims, avoiding a failing
        # deferred read-to-write upgrade even when the busy timeout is set.
        table = connection.ops.quote_name(DispatcherHeartbeat._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f'UPDATE {table} SET state=state WHERE 1=0')
    DispatcherRun.objects.filter(state='running').update(state='interrupted', finished_at=now)
    claimed = list(SessionDispatch.objects.select_for_update().filter(state='claimed').values_list('pk', flat=True))
    SessionDispatch.objects.filter(pk__in=claimed).update(state='uncertain', updated_at=now)
    for dispatch_id in claimed:
        SessionDispatchEvent.objects.create(dispatch_id=dispatch_id, state='uncertain',
            detail={'reason': 'preparer restarted while delivery outcome was unconfirmed'})
    ModelJob.objects.filter(state='running').exclude(
        session_dispatch__state__in=['sent', 'acknowledged']).update(
        state='uncertain', outcome_note='توقف المراقب قبل تأكيد نتيجة التسليم؛ راجع الحالة ولا تعِد الإرسال تلقائيًا.')
    run = DispatcherRun.objects.create(pid=pid)
    for key in ('local', 'session_bridge', 'readonly'):
        defaults = dict(pid=pid, state='running', started_at=now, stopped_at=None, last_error='')
        if key == 'session_bridge':
            defaults['startup_method'] = 'Windows — حدث حفظ المهمة وفحص استعادة محلي'
        DispatcherHeartbeat.objects.update_or_create(key=key, defaults=defaults)
    return run


def prepare_startup(pid):
    from execution_context.owner_delivery import recover_unstarted_claims
    try:
        recover_unstarted_claims()
        for attempt in range(5):
            try:
                return recover_state(pid)
            except OperationalError as exc:
                code = getattr(exc.__cause__, 'sqlite_errorcode', None)
                busy = connection.vendor == 'sqlite' and (
                    (code is not None and code & 255 in (5, 6)) or str(exc).lower() in (
                        'database is locked', 'database table is locked'))
                if not busy or attempt == 4:
                    raise
                time.sleep(0.25 * (2 ** attempt))
    except Exception:
        try:
            DispatcherHeartbeat.objects.filter(key__in=['local', 'session_bridge', 'readonly']).update(
                state='failed', updated_at=timezone.now(), last_error='تعذر بدء العامل؛ راجع سجل التشغيل المحلي.')
        except OperationalError:
            pass  # The UI independently rejects an expired heartbeat.
        raise
