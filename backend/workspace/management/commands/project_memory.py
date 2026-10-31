import json
from django.core.management.base import BaseCommand, CommandError
from workspace.models import SessionCharter
from workspace.memory import instructions, document


class Command(BaseCommand):
    help='Read shared project instructions or store and verify database documentation.'
    def add_arguments(self,p):
        p.add_argument('action',choices=['read','document'])
        p.add_argument('--session',required=True)
        p.add_argument('--task')
        p.add_argument('--summary')
        p.add_argument('--evidence',default='{}')
    def handle(self,*args,**o):
        charter=SessionCharter.objects.get(key=o['session'])
        if o['action']=='read':
            body,digest=instructions(charter.project)
            row=document(charter.project,charter.key,'memory-read:'+digest,'قراءة ذاكرة المشروع',{'memory_digest':digest})
            self.stdout.write(json.dumps({'instructions':body,'digest':digest,'verified_receipt':row.pk},ensure_ascii=False));return
        try:
            if not o['task'] or not o['summary']:raise ValueError('المهمة والملخص مطلوبان')
            row=document(charter.project,charter.key,o['task'],o['summary'],json.loads(o['evidence']))
        except (ValueError,TypeError) as exc:raise CommandError(str(exc))
        self.stdout.write(json.dumps({'id':row.pk,'digest':row.digest,'verified':True}))
