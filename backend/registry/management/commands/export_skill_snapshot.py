import hashlib
import json
import uuid
import zipfile
from pathlib import PurePosixPath
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from registry.models import ServiceVersion, SkillFileSnapshot

class Command(BaseCommand):
    help = 'Export an archived skill version to a new ZIP; never overwrites or executes skill files.'
    def add_arguments(self,parser): parser.add_argument('version')
    def handle(self,*args,**options):
        try: version=ServiceVersion.objects.get(pk=options['version'])
        except (ValueError,ServiceVersion.DoesNotExist) as exc: raise CommandError('Version not found') from exc
        snapshots=list(SkillFileSnapshot.objects.filter(version=version).select_related('blob'))
        if not snapshots: raise CommandError('No archived content exists for this historical version.')
        # Validate all members before creating the archive.
        for snapshot in snapshots:
            path=PurePosixPath(snapshot.path)
            if path.is_absolute() or '..' in path.parts or '\\' in snapshot.path or ':' in snapshot.path:
                raise CommandError('Unsafe archive member path')
            if hashlib.sha256(bytes(snapshot.blob.content)).hexdigest()!=snapshot.blob_id:
                raise CommandError('Archived content failed integrity validation')
        folder=settings.BASE_DIR/'.local'/'skill-exports'
        folder.mkdir(parents=True,exist_ok=True)
        output=folder/f'{version.pk}-{uuid.uuid4().hex[:8]}.zip'
        manifest=[]
        with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for snapshot in snapshots:
                row={'path':snapshot.path,'kind':snapshot.kind,'sha256':snapshot.blob_id}
                if snapshot.kind=='file': archive.writestr('files/'+snapshot.path,bytes(snapshot.blob.content))
                else: row['link_target']=bytes(snapshot.blob.content).decode('utf-8')
                manifest.append(row)
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        self.stdout.write(str(output))
