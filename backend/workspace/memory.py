import hashlib
import json
from django.db import transaction
from .models import ProjectMemory, ProjectDocumentation
from .instruction_text import merge_instructions


CONTEXT_MODULES={
    'core':(), 'text':(), 'calculation':(),
    'code':('code','release'), 'design':('code','design','release'),
    'connected':('connector',), 'prototype':('code','prototype','release'),
    'release':('release',),
}


def instructions(project,kind='core',*,include_core=True):
    if kind not in CONTEXT_MODULES:
        raise ValueError('نوع سياق غير معروف')
    selected={'context.'+module for module in CONTEXT_MODULES[kind]}
    available=list(ProjectMemory.objects.filter(project=project,enabled=True).order_by('key'))
    known={'context.'+module for modules in CONTEXT_MODULES.values() for module in modules}
    if any(row.key.startswith('context.') and row.key not in known for row in available):
        raise ValueError('وحدة تعليمات غير معروفة؛ تحقق من اسمها قبل بناء السياق')
    rows=[row for row in available
          if (include_core and not row.key.startswith('context.')) or row.key in selected]
    text=merge_instructions([row.instruction for row in rows])
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
