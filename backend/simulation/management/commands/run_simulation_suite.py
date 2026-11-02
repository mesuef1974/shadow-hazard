import json
from django.core.management.base import BaseCommand
from simulation.services import save_run, suite


class Command(BaseCommand):
    help = 'Run and persist the bounded scenario catalogue, optionally with a 27-case sweep.'

    def add_arguments(self, parser):
        parser.add_argument('--mode', choices=['synthetic', 'vision'], default='synthetic')
        parser.add_argument('--seed', type=int, default=1)
        parser.add_argument('--grid', action='store_true')

    def handle(self, *args, **options):
        ids = []
        for scenario in suite(options['mode'], options['seed'], options['grid']):
            row = save_run(scenario)
            ids.append(row.pk)
        self.stdout.write(json.dumps({'saved': len(ids), 'ids': ids}))
