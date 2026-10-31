import hashlib
import json
from django.db import transaction
from .models import ProjectMemory, ProjectDocumentation


def instructions(project):
    rows=list(ProjectMemory.objects.filter(project=project,enabled=True).order_by('key'))
    text='\n\n'.join(row.instruction for row in rows)
    return text,hashlib.sha256(text.encode()).hexdigest()


@transaction.atomic
def document(project,session_key,task_key,summary,evidence):
    if not summary.strip() or not isinstance(evidence,dict) or not evidence:
        raise ValueError('التوثيق يتطلب ملخصًا وأدلة')
    payload={'summary':summary,'evidence':evidence}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    row,created=ProjectDocumentation.objects.get_or_create(project=project,session_key=session_key,task_key=task_key,
        defaults={**payload,'digest':digest})
    if row.digest!=digest:raise ValueError('مفتاح التوثيق مستخدم بمحتوى مختلف')
    persisted=ProjectDocumentation.objects.get(pk=row.pk)
    checked=hashlib.sha256(json.dumps({'summary':persisted.summary,'evidence':persisted.evidence},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if checked!=digest:raise ValueError('فشل التحقق من حفظ التوثيق')
    return persisted
