from django.core.management.base import BaseCommand

from automation_log.services import sync


class Command(BaseCommand):
    help = 'Collect automation events into the history log; safe to repeat.'

    def handle(self, *args, **options):
        self.stdout.write(str(sync()))
