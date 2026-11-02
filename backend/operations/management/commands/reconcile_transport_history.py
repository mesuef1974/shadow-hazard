import json
from django.core.management.base import BaseCommand
from operations.reconciliation import reconcile_transport_history


class Command(BaseCommand):
    help = 'Reconcile selected transport labels from persisted turn evidence; never sends or adds usage.'

    def add_arguments(self, parser):
        parser.add_argument('--job', type=int, action='append', required=True)

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(reconcile_transport_history(options['job'])))
