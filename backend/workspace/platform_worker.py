"""The platform's only background worker after Codex was retired (ADR 0002, decisions in force).

Every 30 seconds, token-free: the daily database backup when due, and the Claude usage import.
It touches no Codex session, job lane, capacity or renewal code; those stay in the repository unused.
"""
import os
import threading

from django.conf import settings
from django.db import OperationalError, close_old_connections
from django.utils import timezone

from .models import DispatcherHeartbeat, DispatcherRun
from .worker_lock import single_worker

CYCLE_SECONDS = 30
RETIRED_BEATS = ('session_bridge', 'readonly')


def maintenance():
    """One cycle. Returns the errors as (operation, error type) pairs; never raises."""
    errors = []
    from operations.services import run_due_backup
    from claude_usage.services import import_usage
    for name, operation in (('backup', run_due_backup), ('claude_usage', import_usage)):
        try:
            result = operation()
            if name == 'claude_usage' and not result.ok:
                errors.append((name, result.error or 'ImportFailed'))
        except Exception as exc:  # A failing step is shown on the heartbeat; the next one still runs.
            errors.append((name, type(exc).__name__))
    return errors


def _beat(pid, **fields):
    DispatcherHeartbeat.objects.update_or_create(key='local', defaults={'pid': pid, **fields})


def run(stdout, watch=True, cycles=None):
    with single_worker(settings.BASE_DIR / '.local' / 'model-dispatcher.lock') as acquired:
        if not acquired:
            stdout.write('العامل يعمل بالفعل؛ لن تبدأ نسخة ثانية.')
            return
        pid, now = os.getpid(), timezone.now()
        DispatcherRun.objects.filter(state='running').update(state='interrupted', finished_at=now)
        record = DispatcherRun.objects.create(pid=pid)
        _beat(pid, state='running', started_at=now, stopped_at=None, last_error='',
              startup_method='Windows — مهمة مجدولة (عامل المنصة)')
        DispatcherHeartbeat.objects.filter(key__in=RETIRED_BEATS).update(
            state='retired', stopped_at=now, last_error='أُوقف مع Codex في 4 نوفمبر 2026')
        stop, final = threading.Event(), 'stopped'
        try:
            done = 0
            while True:
                close_old_connections()
                errors = maintenance()
                detail = '; '.join(f'{name}: {kind}' for name, kind in errors)
                try:
                    _beat(pid, state='running', last_error=('maintenance: ' + detail)[:200] if errors else '')
                except OperationalError:
                    pass
                done += 1
                if not watch or (cycles and done >= cycles):
                    break
                stop.wait(CYCLE_SECONDS)
        except KeyboardInterrupt:
            pass
        except Exception:
            final = 'failed'
            raise
        finally:
            finished = timezone.now()
            DispatcherHeartbeat.objects.filter(key='local', pid=pid).update(state=final, stopped_at=finished)
            DispatcherRun.objects.filter(pk=record.pk).update(state=final, finished_at=finished)
