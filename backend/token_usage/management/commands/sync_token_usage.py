import time
from django.core.management.base import BaseCommand
from token_usage.services import synchronize

class Command(BaseCommand):
    help='Import only explicitly tracked project sessions from the local read-only collector database.'
    def add_arguments(self,parser):parser.add_argument('--watch',action='store_true')
    def handle(self,*args,**options):
        while True:
            run=synchronize()
            self.stdout.write(f'{run.success}: {run.added} samples. {run.message}')
            if not options['watch']:break
            time.sleep(30)
