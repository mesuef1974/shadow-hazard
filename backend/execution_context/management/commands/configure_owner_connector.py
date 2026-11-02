import hashlib
import json
import os
from pathlib import Path
import shutil
from django.core.management.base import BaseCommand,CommandError
from workspace.models import SessionCharter
from execution_context.owner_tools import OwnerTools,save_config
from execution_context.owner_channel import bootstrap_trust


class Command(BaseCommand):
    help='Configure and read-test the official local Codex owner MCP connector; no model request.'

    def add_arguments(self,p):p.add_argument('--session',default='backend')

    def handle(self,*args,**opts):
        charter=SessionCharter.objects.select_related('project').get(key=opts['session'])
        pipe=os.environ.get('CODEX_APP_TOOLS_PIPE_PATH','')
        node=os.environ.get('CODEX_MCP_NODE_PATH') or shutil.which('node')
        root=Path.home()/'.codex/plugins/cache/openai-bundled/codex-app-tools'
        servers=list(root.glob('*/server.mjs'))
        if not pipe or not node or not servers:raise CommandError('شغّل التهيئة من جلسة Codex المحلية ذات القناة المصدرة')
        server=max(servers,key=lambda p:p.stat().st_mtime)
        owner=charter.project.sessioncharter_set.filter(key=opts['session']).first()
        from django.contrib.auth import get_user_model
        user=get_user_model().objects.get(username='mesuef',role='supervisor',is_active=True)
        config={'enabled':True,'node':node,'server':str(server),'server_sha256':hashlib.sha256(server.read_bytes()).hexdigest(),
            'pipe':pipe,'caller_thread_id':owner.thread_id,'project_id':charter.project_id,'authorizing_user_id':user.pk}
        config['owner_identity']=bootstrap_trust(pipe)
        import uuid
        with OwnerTools(config) as client:
            data=client.call('read_thread',{'threadId':charter.thread_id,'turnLimit':1,'includeOutputs':False},
                call_id=str(uuid.uuid4()),context_id=str(uuid.uuid4()))
            if data.get('thread',{}).get('id')!=charter.thread_id:raise CommandError('هوية المضيف غير مطابقة')
        save_config(config)
        self.stdout.write(json.dumps({'configured':True,'project_id':charter.project_id,'read_verified':True,'model_request_sent':False}))
