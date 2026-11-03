import time
from contextlib import nullcontext
from django.core.management.base import BaseCommand, CommandError
from token_usage.services import synchronize
from token_usage.locking import local_lock


class Command(BaseCommand):
    help = 'Synchronize tracked sessions; optionally watch every 60 seconds.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **options):
        if not 10 <= options['interval'] <= 3600:
            raise CommandError('Interval must be between 10 and 3600 seconds.')
        with (local_lock('token-usage-watch.lock') if options['watch'] else nullcontext(True)) as acquired:
            if options['watch'] and not acquired:
                self.stdout.write('مراقب المزامنة يعمل بالفعل.')
                return
            while True:
                run = synchronize()
                if run is not None:
                    self.stdout.write(f'{run.success}: {run.added} samples. {run.message}')
                else:
                    self.stdout.write('توجد مزامنة قيد التنفيذ؛ تم تجاوز هذه الدورة.')
                if not options['watch']:
                    break
                time.sleep(options['interval'])
