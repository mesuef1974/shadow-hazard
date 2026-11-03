import json
from django.core.management.base import BaseCommand, CommandError
from workspace.bootstrap import prepare_bootstrap
from workspace.models import SessionCharter, ProjectDocumentation


class Command(BaseCommand):
    help = 'Prepare a compact database-backed bootstrap; never starts a model.'

    def add_arguments(self, parser):
        parser.add_argument('--session', required=True)
        parser.add_argument('--handover', type=int, required=True)

    def handle(self, *args, **options):
        try:
            result = prepare_bootstrap(SessionCharter.objects.get(key=options['session']), options['handover'])
        except (ValueError, SessionCharter.DoesNotExist, ProjectDocumentation.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
