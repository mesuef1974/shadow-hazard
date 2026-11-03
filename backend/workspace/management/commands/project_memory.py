import json
from django.core.management.base import BaseCommand, CommandError
from workspace.models import SessionCharter, ProjectDocumentation
from workspace.memory import instructions, document, CONTEXT_MODULES


class Command(BaseCommand):
    help='Read shared project instructions or store and verify database documentation.'
    def add_arguments(self,p):
        p.add_argument('action',choices=['read','document','inspect'])
        p.add_argument('--document-id',type=int)
        p.add_argument('--document-ids',type=int,nargs='+',help='Verify several receipts in one read; cannot combine with --document-id.')
        p.add_argument('--compact',action='store_true',help='Bound inspect output; omitted evidence remains available through a full inspect.')
        p.add_argument('--session',required=True)
        p.add_argument('--task')
        p.add_argument('--summary')
        p.add_argument('--evidence',default='{}')
        p.add_argument('--kind',choices=list(CONTEXT_MODULES),default='core')
        p.add_argument('--details-only',action='store_true',help='Read task-specific rules without repeating already loaded core rules.')
    def handle(self,*args,**o):
        charter=SessionCharter.objects.get(key=o['session'])
        if o['action']=='inspect':
            from token_usage.capacity import verified_document
            if o['document_id'] and o['document_ids']:
                raise CommandError('اختر --document-id أو --document-ids فقط')
            ids=list(dict.fromkeys(o['document_ids'] or [o['document_id']]))
            if not all(ids) or len(ids)>12:
                raise CommandError('يلزم من إيصال واحد إلى 12 إيصالًا')
            result=[]
            for pk in ids:
                row=ProjectDocumentation.objects.filter(pk=pk,project=charter.project).first()
                if not row or not verified_document(row):
                    raise CommandError('إيصال غير موجود في المشروع أو تعذر التحقق من سلامته')
                item={'id':row.pk,'session_key':row.session_key,'task_key':row.task_key,
                    'summary':row.summary,'evidence':row.evidence,'digest':row.digest,'verified':True}
                if o['compact']:
                    item['summary']=row.summary[:600]
                    # Verify the full record before bounding display. Never imply
                    # omitted material was loaded into the model's context.
                    bounded={}
                    for key,value in list(row.evidence.items())[:12]:
                        if len(key)>80 or not (value is None or type(value) in (bool,int,float,str)):
                            continue
                        encoded=json.dumps(value,ensure_ascii=False)
                        if len(encoded)>120:continue
                        candidate={**bounded,key:value}
                        if len(json.dumps(candidate,ensure_ascii=False))<=800:bounded=candidate
                    item['evidence']=bounded
                    item['omitted']=len(row.summary)>600 or item['evidence']!=row.evidence
                    item['full_read']='project_memory inspect --session '+charter.key+' --document-id '+str(row.pk)
                result.append(item)
            self.stdout.write(json.dumps(result if o['document_ids'] else result[0],ensure_ascii=False))
            return
        if o['action']=='read':
            body,digest=instructions(charter.project,o['kind'],include_core=not o['details_only'])
            row=document(charter.project,charter.key,'memory-read:'+digest,'قراءة ذاكرة المشروع',{'memory_digest':digest})
            self.stdout.write(json.dumps({'instructions':body,'digest':digest,'verified_receipt':row.pk},ensure_ascii=False));return
        try:
            if not o['task'] or not o['summary']:raise ValueError('المهمة والملخص مطلوبان')
            row=document(charter.project,charter.key,o['task'],o['summary'],json.loads(o['evidence']))
        except (ValueError,TypeError) as exc:raise CommandError(str(exc))
        from token_usage.capacity import verified_document
        row=ProjectDocumentation.objects.get(pk=row.pk)
        if not verified_document(row):raise CommandError('فشل التحقق من إعادة قراءة إيصال التوثيق')
        self.stdout.write(json.dumps({'id':row.pk,'digest':row.digest,'verified':True,'reread_verified':True}))
