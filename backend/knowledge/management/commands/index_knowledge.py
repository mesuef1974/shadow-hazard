from django.core.management.base import BaseCommand, CommandError
from workspace.models import Project
from knowledge.services import index_project


class Command(BaseCommand):
    help = 'Refresh active memory and supervisor decisions; explicitly select documentation summaries.'

    def add_arguments(self, parser):
        parser.add_argument('--project', type=int, required=True)
        parser.add_argument('--documentation', nargs='+', type=int, default=[])

    def handle(self, *args, **options):
        try:
            project = Project.objects.get(pk=options['project'])
            count = index_project(project, options['documentation'])
        except (Project.DoesNotExist, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Indexed {count} sources.')
