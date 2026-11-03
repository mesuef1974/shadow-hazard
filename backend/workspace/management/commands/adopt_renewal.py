from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from execution_context.rollout_usage import RolloutEvidenceError
from execution_context.owner_tools import OwnerToolsError
from workspace.renewal import RenewalError
from workspace.renewal_adoption import authorize_adoption, commit_adoption


class Command(BaseCommand):
    help='Explicit adoption of an existing renewal candidate; no messages or new threads.'

    def add_arguments(self, parser):
        parser.add_argument('action',choices=['authorize','commit'])
        parser.add_argument('--request',type=int,required=True)
        parser.add_argument('--owner',required=True)
        parser.add_argument('--instruction',default='')
        parser.add_argument('--approval',type=int)
        parser.add_argument('--accept-historical-gaps',action='store_true')

    def handle(self,*args,**options):
        try:
            actor=get_user_model().objects.get(username=options['owner'])
            if options['action']=='authorize':
                receipt=authorize_adoption(options['request'],actor,user_instruction=options['instruction'],
                    accept_historical_gaps=options['accept_historical_gaps'])
                self.stdout.write(f'Authorization #{receipt.pk}; binding unchanged.')
                self.stdout.write(receipt.evidence['acknowledgement'])
            else:
                if not options['approval']:raise CommandError('--approval is required')
                receipt=commit_adoption(options['request'],actor,approval_id=options['approval'])
                self.stdout.write(f'Adoption completed; receipt #{receipt.pk}.')
        except (RenewalError,RolloutEvidenceError,OwnerToolsError) as exc:
            raise CommandError(str(exc)) from exc
