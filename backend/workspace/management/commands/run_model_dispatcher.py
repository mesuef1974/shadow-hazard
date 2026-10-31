import time
from django.core.management.base import BaseCommand
from workspace.dispatcher import run_one

class Command(BaseCommand):
    help='Run the local serialized Codex task dispatcher; no automatic retries.'
    def add_arguments(self,parser):parser.add_argument('--watch',action='store_true')
    def handle(self,*args,**options):
        while True:
            job=run_one()
            if job:self.stdout.write(f'Job {job.pk}: {job.state}')
            if not options['watch']:break
            time.sleep(5)
