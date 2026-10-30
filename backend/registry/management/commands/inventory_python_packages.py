import sys
import platform
from importlib.metadata import distributions
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from registry.models import PythonPackage

class Command(BaseCommand):
    help = 'Inventory distributions in the active project virtual environment.'
    @transaction.atomic
    def handle(self, *args, **options):
        if sys.prefix == sys.base_prefix:
            raise CommandError('Run with the project virtual environment Python.')
        from django.conf import settings
        from pathlib import Path
        if Path(sys.prefix).resolve() != (settings.BASE_DIR.parent / '.venv').resolve():
            raise CommandError('This command inventories only the project .venv.')
        observed = timezone.now()
        names = []
        for dist in distributions():
            name = dist.metadata.get('Name')
            if not name: continue
            name = name.lower().replace('_', '-').replace('.', '-')
            names.append(name)
            PythonPackage.objects.update_or_create(name=name, defaults={
                'version':dist.version, 'summary':dist.metadata.get('Summary',''),
                'requires':dist.requires or [], 'location':str(dist.locate_file('')),
                'python_version':platform.python_version(), 'environment':sys.prefix,
                'is_installed':True, 'observed_at':observed,
            })
        PythonPackage.objects.exclude(name__in=names).update(is_installed=False)
        self.stdout.write(f'Inventoried {len(set(names))} installed distributions; no packages installed or removed.')
