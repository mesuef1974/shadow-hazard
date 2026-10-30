from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from registry.models import Installation, ExecutionSession, RegistryTask
from registry.services import record_usage

class Command(BaseCommand):
    help = 'Record a declared skill use with immutable version, actor, task and purpose.'
    def add_arguments(self, parser):
        for name in ['event','installation','session','actor','task','reason','method','result']:
            parser.add_argument('--'+name, required=True)
    @transaction.atomic
    def handle(self,*args,**options):
        if any(not options[key].strip() for key in ['event','installation','session','actor','task','reason','method','result']):
            raise CommandError('All usage fields must be nonempty.')
        try:
            installation=Installation.objects.select_related('version').get(external_key=options['installation'],presence='file_verified')
            session=ExecutionSession.objects.select_related('actor').get(external_key=options['session'])
            task=RegistryTask.objects.get(key=options['task'])
        except (Installation.DoesNotExist,ExecutionSession.DoesNotExist,RegistryTask.DoesNotExist) as exc:
            raise CommandError('Installation, session or task not found or skill removed.') from exc
        if session.actor.key != options['actor']:
            raise CommandError('Actor must match the session owner.')
        from registry.models import UsageEvent
        previous=UsageEvent.objects.filter(event_key=options['event']).first()
        version=previous.version if previous else installation.version
        record_usage(event_key=options['event'],version=version,installation=installation,session=session,task=task,
                     recorded_at=timezone.now(),purpose=options['reason'],method=options['method'],result=options['result'],
                     time_evidence='Declared use; timestamp is registration time, not inferred execution time.',status='completed')
        self.stdout.write('Skill use recorded.')
