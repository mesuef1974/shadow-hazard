"""Prepare a bounded session handover in code, without a model preparation turn."""
import hashlib
import json
from .memory import instructions, document
from .models import ProjectDocumentation


def prepare_bootstrap(charter, handover_id):
    handover = ProjectDocumentation.objects.get(
        pk=handover_id, project=charter.project, session_key=charter.key)
    payload_digest = hashlib.sha256(json.dumps(
        {'summary': handover.summary, 'evidence': handover.evidence},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if payload_digest != handover.digest:
        raise ValueError('بصمة التسليم لا تطابق المحتوى المحفوظ')
    memory, digest = instructions(charter.project)
    # Summary is the agreed handover. Historical evidence stays in the database.
    summary = handover.summary
    if len(summary) > 2000:
        raise ValueError('ملخص التسليم أكبر من ميزانية التهيئة؛ اختصره واعتمده قبل إنشاء الجلسة')
    payload = {'handover_id': handover.pk, 'handover_digest': handover.digest,
               'memory_digest': digest, 'scope': charter.scope, 'rules': charter.rules,
               'summary': summary, 'memory': memory}
    receipt = document(charter.project, charter.key,
        f'bootstrap:{handover.pk}:{digest}', 'تحميل تسليم الجلسة وذاكرة المشروع بواسطة المضيف', payload)
    prompt = (f'تهيئة جلسة {charter.name}. قرأ المضيف ذاكرة المشروع وتسليمها من القاعدة '
              f'وتحقق من الحفظ بإيصال {receipt.pk}. لا توجد مهمة تنفيذ الآن.\n'
              f'النطاق: {charter.scope}\nالميثاق: {charter.rules}\n'
              f'تعليمات المشروع المحملة: {memory}\nملخص التسليم: {summary}\n'
              f'مرجع التفاصيل عند الحاجة فقط: ProjectDocumentation #{handover.pk}. '
              'لا تحمل التاريخ أو كتالوج المهارات؛ اقرأ المهارة أو المرجع المحدد عند الحاجة لمهمة فعلية. '
              'لا تشغّل أدوات أو تعيد قراءة الذاكرة للتهيئة؛ أعد فقط CONTEXT_READY.')
    if len(prompt) > 6000:
        raise ValueError('تجاوزت تعليمات التهيئة الميزانية؛ لا اقتطاع للتعليمات الملزمة')
    return {'prompt': prompt, 'receipt': receipt.pk, 'characters': len(prompt)}
