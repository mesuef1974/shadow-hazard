import uuid
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model
from workspace.models import SessionCharter
from execution_context.messages import send_message
from execution_context.models import SessionMessage


class Command(BaseCommand):
    help='Register an authorized inter-session message in the existing job outbox.'
    def add_arguments(self,parser):
        parser.add_argument('--sender',required=True)
        parser.add_argument('--recipient',required=True)
        parser.add_argument('--message',required=True)
        parser.add_argument('--key',type=uuid.UUID,required=True)
        parser.add_argument('--kind',choices=['task','reply','handoff','notice'],default='task')
        parser.add_argument('--reply-to',type=int)
        parser.add_argument('--task-kind',choices=['auto','text','code','design','connected'],default='auto')
    def handle(self,*args,**options):
        try:
            sender=SessionCharter.objects.get(key=options['sender'])
            recipient=SessionCharter.objects.get(key=options['recipient'])
            owner=get_user_model().objects.get(username='mesuef')
            reply=SessionMessage.objects.get(pk=options['reply_to']) if options['reply_to'] else None
            message=send_message(sender=sender,recipient=recipient,owner=owner,body=options['message'],
                message_key=options['key'],kind=options['kind'],reply_to=reply,task_kind=options['task_kind'])
        except (ValueError,SessionCharter.DoesNotExist,SessionMessage.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'message={message.pk} job={message.job_id} state={message.state}')
