import os
import threading
import time
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import OperationalError, close_old_connections, transaction
from django.utils import timezone
from workspace.dispatcher import run_one
from workspace.models import DispatcherHeartbeat, DispatcherRun, ModelJob
from execution_context.models import SessionDispatch, SessionDispatchEvent
from workspace.worker_lock import single_worker
from workspace.wakeup import DispatcherWakeup
from workspace.readonly_runner import ReadonlyRunner
from execution_context.desktop_session_bridge import BridgeError
from execution_context.native_queue_bridge import observe_arrivals, reconcile
from execution_context.delivery import deliver_one
from execution_context.owner_closure import verify_owner_closures
from workspace.automation_hooks import automation_cycle

class Command(BaseCommand):
    help='Prepare jobs and deliver approved workspace-write work to existing Codex sessions.'
    def add_arguments(self,parser):parser.add_argument('--watch',action='store_true')
    def handle(self,*args,**options):
        if getattr(settings,'CODEX_RETIRED',False):
            # Codex is retired: the same scheduled task now runs only the platform worker.
            from workspace.platform_worker import run
            return run(self.stdout,watch=options['watch'])
        with single_worker(settings.BASE_DIR/'.local'/'model-dispatcher.lock') as acquired:
            if not acquired:
                self.stdout.write('المراقب يعمل بالفعل؛ لن تبدأ نسخة ثانية.')
                return
            from workspace.worker_startup import prepare_startup
            run=prepare_startup(os.getpid())
            readonly_runner=ReadonlyRunner()
            wakeup=DispatcherWakeup()
            stop=threading.Event()
            def pulse():
                try:
                    while not stop.wait(5):
                        close_old_connections()
                        try:
                            now=timezone.now()
                            DispatcherHeartbeat.objects.filter(key__in=['local','session_bridge','readonly'],pid=os.getpid()).update(updated_at=now)
                        except OperationalError:pass
                finally:close_old_connections()
            monitor=threading.Thread(target=pulse,daemon=True);monitor.start()
            final_state='stopped'
            last_maintenance=0.0
            try:
                while True:
                    close_old_connections()
                    job=run_one()
                    if options['watch']:
                        readonly_runner.start_if_pending()
                    delivered=None
                    try:
                        observe_arrivals()
                        reconcile()
                        delivered=deliver_one()
                        closure=verify_owner_closures()
                        warning='حرج: رفض دليل إغلاق قديم؛ أحداث '+','.join(map(str,closure['rejected'])) if closure['rejected'] else ''
                        DispatcherHeartbeat.objects.filter(key='session_bridge',pid=os.getpid()).update(last_error=warning[:200])
                    except BridgeError as error:
                        DispatcherHeartbeat.objects.filter(key='session_bridge',pid=os.getpid()).update(last_error=str(error)[:200])
                        if not options['watch']:
                            raise
                    if job:self.stdout.write(f'Job {job.pk}: {job.state}')
                    if delivered:self.stdout.write(f'Session dispatch {delivered}: processed')
                    if options['watch'] and time.monotonic()-last_maintenance>=30:
                        maintenance=automation_cycle()
                        from token_usage.capacity import maintenance as capacity_maintenance
                        capacity_result=capacity_maintenance()
                        maintenance.setdefault('errors',[]).extend(capacity_result['errors'])
                        from workspace.automation_hooks import auto_renewal_cycle
                        renewal_result=auto_renewal_cycle()
                        maintenance.setdefault('errors',[]).extend(renewal_result['errors'])
                        from operations.operator_monitor import maintenance as operator_maintenance
                        operator_result=operator_maintenance()
                        maintenance.setdefault('errors',[]).extend(operator_result['errors'])
                        from execution_context.container_reaper import maintenance as container_maintenance
                        maintenance.setdefault('errors',[]).extend(container_maintenance()['errors'])
                        last_maintenance=time.monotonic()
                        errors=maintenance.get('errors',[])
                        if errors:
                            detail='; '.join(f"{e['operation']}: {e['error_type']}" for e in errors)
                            DispatcherHeartbeat.objects.filter(key='local',pid=os.getpid()).update(last_error='maintenance: '+detail[:170])
                        else:
                            DispatcherHeartbeat.objects.filter(key='local',pid=os.getpid()).update(last_error='')
                    if not options['watch'] or stop.is_set():break
                    if job or delivered:
                        continue
                    # Submission signals this auto-reset OS event after commit.
                    # The bounded timeout only recovers missed signals and costs no tokens.
                    wakeup.wait(30)
            except KeyboardInterrupt:pass
            except Exception:
                final_state='failed'
                DispatcherHeartbeat.objects.filter(key='local').update(last_error='تعطل المراقب؛ راجع سجل التشغيل المحلي.')
                raise
            finally:
                stop.set();monitor.join(timeout=6)
                now=timezone.now()
                DispatcherHeartbeat.objects.filter(key__in=['local','session_bridge','readonly'],pid=os.getpid()).update(state=final_state,stopped_at=now,updated_at=now)
                DispatcherRun.objects.filter(pk=run.pk).update(state=final_state,finished_at=timezone.now())
                wakeup.close()
