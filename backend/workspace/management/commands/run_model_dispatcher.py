import time
from django.core.management.base import BaseCommand
from workspace.dispatcher import run_one
from workspace.models import DispatcherHeartbeat
from django.db import OperationalError, close_old_connections

class Command(BaseCommand):
    help='Run the local serialized Codex task dispatcher; no automatic retries.'
    def add_arguments(self,parser):parser.add_argument('--watch',action='store_true')
    def handle(self,*args,**options):
        while True:
            close_old_connections()
            try:
                DispatcherHeartbeat.objects.update_or_create(key='local',defaults={})
                job=run_one()
                if job:self.stdout.write(f'Job {job.pk}: {job.state}')
            except OperationalError:
                if not options['watch']:raise
            if not options['watch']:break
            time.sleep(5)
