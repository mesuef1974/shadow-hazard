import hashlib
import mimetypes
from django.core.management.base import BaseCommand
from registry.models import Artifact
from registry.portal import ROOT, EXTENSIONS


class Command(BaseCommand):
    help = "Register current document fingerprints without deleting historical versions."

    def handle(self, *args, **options):
        added = 0
        for path in ROOT.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
                continue
            name = path.relative_to(ROOT)
            if not path.resolve().is_relative_to(ROOT.resolve()) or any(part.startswith('.') for part in name.parts):
                continue
            _, created = Artifact.objects.get_or_create(
                path='docs/' + name.as_posix(),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                defaults={'media_type': mimetypes.guess_type(path.name)[0] or 'text/plain'},
            )
            added += created
        self.stdout.write(f'Registered {added} new document versions; history preserved.')
