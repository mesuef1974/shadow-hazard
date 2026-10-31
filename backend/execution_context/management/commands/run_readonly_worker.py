import json
import time
from django.core.management.base import BaseCommand,CommandError
from django.db import close_old_connections
from django.conf import settings
from workspace.worker_lock import single_worker
from execution_context.readonly_service import run_pending


class Command(BaseCommand):
    help='Run prepared read-only tasks in disposable containers and verify DB documentation.'
    def add_arguments(self,p):
        p.add_argument('--job',type=int)
        p.add_argument('--watch',action='store_true')
    def handle(self,*args,**o):
        with single_worker(settings.BASE_DIR/'.local'/'model-dispatcher.lock') as acquired:
            if not acquired:raise CommandError('عامل آخر يعمل؛ لن تبدأ نسخة متعارضة')
            while True:
                close_old_connections()
                try:job=run_pending(o['job'])
                except Exception as exc:raise CommandError(str(exc)) from exc
                self.stdout.write(json.dumps({'job':job.pk,'state':job.state} if job else {}))
                if not o['watch'] or (o['job'] and job):return
                time.sleep(5)
