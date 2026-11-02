import json
import uuid
from django.core.management.base import BaseCommand,CommandError
from workspace.models import SessionCharter
from execution_context.owner_tools import OwnerTools,OwnerToolsError,load_config
from execution_context.owner_delivery import _idle


class Command(BaseCommand):
    help='Read-test the configured official owner connector; never sends a model request.'

    def add_arguments(self,p):p.add_argument('--session',default='review')

    def handle(self,*args,**opts):
        try:
            config=load_config()
            charter=SessionCharter.objects.get(key=opts['session'],project_id=config['project_id'])
            with OwnerTools(config) as client:
                idle=_idle(client,charter.thread_id,str(uuid.uuid4()))
            self.stdout.write(json.dumps({'connected':True,'session':charter.key,'identity_verified':True,'idle':idle,'model_request_sent':False}))
        except OwnerToolsError as exc:raise CommandError(str(exc)) from exc
