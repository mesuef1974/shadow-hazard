from pathlib import Path

from django.core.management.base import BaseCommand

from claude_usage.services import default_sources, import_usage


class Command(BaseCommand):
    help = 'Import Claude token usage (numbers only) from Claude Code transcript files.'

    def add_arguments(self, parser):
        parser.add_argument('--path', action='append', default=[],
                            help='Extra directory of exported session files (no repository filter).')

    def handle(self, *args, **options):
        run = import_usage(default_sources() + [('session_export', Path(p), False) for p in options['path']])
        status = 'ok' if run.ok else f'error {run.error}'
        self.stdout.write(f'{status}: files {run.files_read}, lines {run.lines_read}, new {run.messages_new}, '
                          f'updated {run.messages_updated}, skipped {run.skipped_lines}')
