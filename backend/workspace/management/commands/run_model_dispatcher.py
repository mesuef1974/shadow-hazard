import os
import threading
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import OperationalError, close_old_connections, transaction
from django.utils import timezone
from workspace.dispatcher import run_one
from workspace.models import DispatcherHeartbeat, DispatcherRun, ModelJob
from execution_context.models import SessionDispatch, SessionDispatchEvent
from workspace.worker_lock import single_worker
from workspace.wakeup import DispatcherWakeup
from execution_context.desktop_session_bridge import BridgeError
from execution_context.native_queue_bridge import observe_arrivals, reconcile
from execution_context.delivery import deliver_one
from workspace.automation_hooks import automation_cycle

class Command(BaseCommand):
    help='Prepare jobs and deliver approved workspace-write work to existing Codex sessions.'
    def add_arguments(self,parser):parser.add_argument('--watch',action='store_true')
    def handle(self,*args,**options):
        with single_worker(settings.BASE_DIR/'.local'/'model-dispatcher.lock') as acquired:
            if not acquired:
                self.stdout.write('المراقب يعمل بالفعل؛ لن تبدأ نسخة ثانية.')
                return
            now=timezone.now()
            DispatcherRun.objects.filter(state='running').update(state='interrupted',finished_at=now)
            # The preparer is not the owner of an active desktop thread. Preserve work
            # already sent/acknowledged; only a claimed but unconfirmed delivery is uncertain.
            with transaction.atomic():
                claimed_ids=list(SessionDispatch.objects.filter(state='claimed').values_list('pk',flat=True))
                SessionDispatch.objects.filter(pk__in=claimed_ids).update(state='uncertain',updated_at=now)
                for dispatch_id in claimed_ids:
                    SessionDispatchEvent.objects.create(dispatch_id=dispatch_id,state='uncertain',
                        detail={'reason':'preparer restarted while delivery outcome was unconfirmed'})
                ModelJob.objects.filter(state='running').exclude(session_dispatch__state__in=['sent','acknowledged']).update(
                    state='uncertain',outcome_note='توقف المراقب قبل تأكيد نتيجة التسليم؛ راجع الحالة ولا تعِد الإرسال تلقائيًا.')
            run=DispatcherRun.objects.create(pid=os.getpid())
            DispatcherHeartbeat.objects.update_or_create(key='local',defaults={
                'pid':os.getpid(),'state':'running','started_at':now,'stopped_at':None,
                'last_error':''})
            DispatcherHeartbeat.objects.update_or_create(key='session_bridge',defaults={
                'pid':os.getpid(),'state':'running','started_at':now,'stopped_at':None,
                'last_error':'','startup_method':'Windows — حدث حفظ المهمة وفحص استعادة محلي'})
            wakeup=DispatcherWakeup()
            stop=threading.Event()
            def pulse():
                try:
                    while not stop.wait(5):
                        close_old_connections()
                        try:
                            now=timezone.now()
                            DispatcherHeartbeat.objects.filter(key__in=['local','session_bridge'],pid=os.getpid()).update(updated_at=now)
                        except OperationalError:pass
                finally:close_old_connections()
            monitor=threading.Thread(target=pulse,daemon=True);monitor.start()
            final_state='stopped'
            try:
                while True:
                    close_old_connections()
                    job=run_one()
                    from execution_context.readonly_service import run_pending
                    readonly=run_pending()
                    delivered=None
                    try:
                        observe_arrivals()
                        reconcile()
                        delivered=deliver_one()
                        DispatcherHeartbeat.objects.filter(key='session_bridge',pid=os.getpid()).update(last_error='')
                    except BridgeError as error:
                        DispatcherHeartbeat.objects.filter(key='session_bridge',pid=os.getpid()).update(last_error=str(error)[:200])
                        if not options['watch']:
                            raise
                    if readonly:self.stdout.write(f'Readonly job {readonly.pk}: {readonly.state}')
                    if job:self.stdout.write(f'Job {job.pk}: {job.state}')
                    if delivered:self.stdout.write(f'Session dispatch {delivered}: processed')
                    if options['watch']:
                        maintenance=automation_cycle()
                        errors=maintenance.get('errors',[])
                        if errors:
                            detail='; '.join(f"{e['operation']}: {e['error_type']}" for e in errors)
                            DispatcherHeartbeat.objects.filter(key='local',pid=os.getpid()).update(last_error='maintenance: '+detail[:170])
                        else:
                            DispatcherHeartbeat.objects.filter(key='local',pid=os.getpid()).update(last_error='')
                    if not options['watch'] or stop.is_set():break
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
                DispatcherHeartbeat.objects.filter(key__in=['local','session_bridge'],pid=os.getpid()).update(state=final_state,stopped_at=now,updated_at=now)
                DispatcherRun.objects.filter(pk=run.pk).update(state=final_state,finished_at=timezone.now())
                wakeup.close()
