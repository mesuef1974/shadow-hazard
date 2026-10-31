import os
import threading
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import OperationalError, close_old_connections
from django.utils import timezone
from workspace.dispatcher import run_one, resolve_codex
from workspace.models import DispatcherHeartbeat, DispatcherRun, ModelJob
from workspace.worker_lock import single_worker

class Command(BaseCommand):
    help='Single local worker with OS lock and persistent health history.'
    def add_arguments(self,parser):parser.add_argument('--watch',action='store_true')
    def handle(self,*args,**options):
        with single_worker(settings.BASE_DIR/'.local'/'model-dispatcher.lock') as acquired:
            if not acquired:
                self.stdout.write('المراقب يعمل بالفعل؛ لن تبدأ نسخة ثانية.')
                return
            now=timezone.now()
            DispatcherRun.objects.filter(state='running').update(state='interrupted',finished_at=now)
            ModelJob.objects.filter(state='running').update(state='uncertain',outcome_note='توقف المراقب قبل تأكيد النتيجة؛ راجع التنفيذ ولا تعِد إرساله تلقائيًا.')
            run=DispatcherRun.objects.create(pid=os.getpid())
            DispatcherHeartbeat.objects.update_or_create(key='local',defaults={
                'pid':os.getpid(),'state':'running','started_at':now,'stopped_at':None,
                'last_error':'' if resolve_codex() else 'لم يُعثر على برنامج Codex؛ يلزم التحقق قبل تنفيذ الطلبات.'})
            stop=threading.Event()
            def pulse():
                try:
                    while not stop.wait(5):
                        close_old_connections()
                        try:DispatcherHeartbeat.objects.filter(key='local',pid=os.getpid()).update(updated_at=timezone.now())
                        except OperationalError:pass
                finally:close_old_connections()
            monitor=threading.Thread(target=pulse,daemon=True);monitor.start()
            final_state='stopped'
            try:
                while True:
                    close_old_connections()
                    job=run_one()
                    if job:self.stdout.write(f'Job {job.pk}: {job.state}')
                    if not options['watch'] or stop.wait(5):break
            except KeyboardInterrupt:pass
            except Exception:
                final_state='failed'
                DispatcherHeartbeat.objects.filter(key='local').update(last_error='تعطل المراقب؛ راجع سجل التشغيل المحلي.')
                raise
            finally:
                stop.set();monitor.join(timeout=6)
                DispatcherHeartbeat.objects.filter(key='local',pid=os.getpid()).update(state=final_state,stopped_at=timezone.now(),updated_at=timezone.now())
                DispatcherRun.objects.filter(pk=run.pk).update(state=final_state,finished_at=timezone.now())
