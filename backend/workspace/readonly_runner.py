"""Run isolated read-only jobs without holding up session message delivery."""
import threading

from django.db import close_old_connections


class ReadonlyRunner:
    def __init__(self, has_pending=None, execute=None):
        self._has_pending = has_pending or self._has_pending_jobs
        self._execute = execute or self._run_pending
        self._guard = threading.Lock()
        self._thread = None

    @staticmethod
    def _has_pending_jobs():
        from workspace.job_lanes import available_jobs
        from workspace.models import ModelJob

        jobs = ModelJob.objects.filter(mode='read-only', state='waiting_readonly', session_dispatch__isnull=True)
        return available_jobs(jobs).exists()

    @staticmethod
    def _run_pending():
        from execution_context.readonly_service import run_pending

        job = run_pending()
        if job:
            from workspace.models import DispatcherHeartbeat

            DispatcherHeartbeat.objects.filter(key='readonly').update(last_error='')

    @property
    def busy(self):
        return bool(self._thread and self._thread.is_alive())

    def start_if_pending(self):
        with self._guard:
            if self.busy or not self._has_pending():
                return False
            self._thread = threading.Thread(target=self._run, name='shadow-danger-readonly', daemon=True)
            self._thread.start()
            return True

    def _run(self):
        close_old_connections()
        try:
            self._execute()
        except Exception as exc:
            from workspace.models import DispatcherHeartbeat

            DispatcherHeartbeat.objects.filter(key='readonly').update(
                last_error=f'تعذر تنفيذ القراءة المعزولة: {type(exc).__name__}'[:200]
            )
        finally:
            close_old_connections()
