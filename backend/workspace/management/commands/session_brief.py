import json
from django.core.management.base import BaseCommand
from workspace.models import SessionCharter

class Command(BaseCommand):
    help='Read a concise session charter from the project database.'
    def add_arguments(self,parser):parser.add_argument('key')
    def handle(self,*args,**options):
        c=SessionCharter.objects.select_related('policy').get(key=options['key'])
        from workspace.memory import instructions,document
        memory,digest=instructions(c.project)
        receipt=document(c.project,c.key,'memory-read:'+digest,'قراءة ذاكرة المشروع',{'memory_digest':digest})
        self.stdout.write(json.dumps({'project_memory':memory,'memory_receipt':receipt.pk,'name':c.name,'scope':c.scope,'rules':c.rules,'model':c.policy.model,'thinking':c.policy.thinking,'state':c.state},ensure_ascii=False,indent=2))
