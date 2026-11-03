"""Prepare an auditable recovery assessment; never authorize or switch a binding."""
import hashlib
import json
from django.db import transaction

from execution_context.renewal_history_evidence import historical_bootstrap_evidence
from .memory import document
from .models import ProjectDocumentation
from .renewal import _lock, _approval, _context, _verified, _stored_evidence, RenewalError


@transaction.atomic
def prepare_recovery(request_id, actor):
    row = _lock(request_id, actor)
    _approval(row)
    _stored_evidence(row)
    if (row.state not in {'created','creation_uncertain','verified'} or not row.create_attempted
            or not row.target_thread_id or row.charter.thread_id != row.source_thread_id):
        raise RenewalError('Recovery requires the recorded, unswitched candidate')
    proof = historical_bootstrap_evidence(row, row.target_thread_id)
    current = _context(row.charter)
    changed = sorted(k for k in set(current) | set(row.context) if current.get(k) != row.context.get(k))
    identity = {'request_id':row.pk,'renewal_key':str(row.key),'source':row.source_thread_id,
                'target':row.target_thread_id,'original_context':row.context,'current_context':current,
                'prefix_sha256':proof['prefix_sha256']}
    digest = hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    key = f'renewal-recovery-preparation:{row.pk}:{digest}'
    previous = ProjectDocumentation.objects.filter(project=row.charter.project,
        session_key=row.charter.key,task_key=key).first()
    if previous:
        if not _verified(previous):
            raise RenewalError('Recovery preparation receipt changed')
        return previous
    return document(row.charter.project,row.charter.key,key,
        'تحقق تاريخي من تسليم التهيئة الأصلية وتحضير استعادة الطلب القائم؛ لم يُعتمد الربط.',
        {'status':'renewal_recovery_prepared_not_authorized', **identity,
         'changed_context_keys':changed, 'historical_bootstrap':proof,
         'binding_authorized':False, 'report_title':'تحضير استعادة التجديد — إثبات تاريخي فقط',
         'report_sections':[
             {'title':'ما تحقق','body':f'تم التحقق من أول دور تهيئة للطلب {row.pk}، بهوية الإنشاء والنص والنموذج والاستجابة المقترنة وغياب التنفيذ، مع بصمة للبادئة التاريخية فقط.'},
             {'title':'السياق الحالي','body':'الحقول المختلفة عن سياق الإنشاء: '+('، '.join(changed) or 'لا اختلاف')+'. السياق الأصلي محفوظ دون استبدال.'},
             {'title':'حدود التحضير','body':'هذا الإيصال لا يثبت خمول الجلسة أو تسليم الذاكرة الحالية ولا يسمح بتغيير الرابط. لم تتغير حالة الطلب أو إيصال التسليم التشغيلي أو وجهة الميثاق.'},
             {'title':'المطلوب قبل الاعتماد','body':'تفويض استعادة مقيد بالطلب والوجهتين والسياق الحالي، وإقرار حديث بتسليم السياق، ثم تحقق مستقل من خمول الطرفين والعمل المعلق وفحص نهائي قبل تبديل الرابط.'},
         ]})
