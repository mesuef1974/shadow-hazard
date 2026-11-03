from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from execution_context.rollout_usage import RolloutEvidenceError
from workspace.renewal import RenewalError
from workspace.renewal_recovery import prepare_recovery


class Command(BaseCommand):
    help = 'Record historical renewal evidence only; never create, send, or rebind.'

    def add_arguments(self, parser):
        parser.add_argument('--request', type=int, required=True)
        parser.add_argument('--owner', required=True)

    def handle(self, *args, **options):
        try:
            actor = get_user_model().objects.get(username=options['owner'])
            receipt = prepare_recovery(options['request'], actor)
        except (get_user_model().DoesNotExist, RenewalError, RolloutEvidenceError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Recovery preparation receipt #{receipt.pk}; binding unchanged.')
