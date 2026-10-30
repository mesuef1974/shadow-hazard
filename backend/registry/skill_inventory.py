"""Observe SKILL.md files as data only; never import or execute skill code."""
import hashlib
import os
import re
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Installation, Service, ServiceVersion, SkillChange, SkillScan

EXPLANATIONS = {
 'minimal-code-edit':'تنفذ تعديلًا برمجيًا محدودًا مع الحفاظ على بقية المشروع.',
 'focused-testing':'تختار الاختبارات المناسبة للتغيير وتتحقق من نتيجتها.',
 'frontend-design':'توجه تصميم واجهات واضحة ومتناسقة مع هوية المشروع.',
 'ui-ux-pro-max':'تساعد في تصميم وتجربة استخدام الواجهات ومراجعتها.',
 'targeted-debugging':'تتبع سبب خطأ محدد وتقترح إصلاحًا يمكن التحقق منه.',
 'playwright':'تؤتمت المتصفح لفحص الصفحات وتدفقات الاستخدام.',
 'computer-use':'تتعامل مع واجهات البرامج على الجهاز.',
 'imagegen':'تنشئ الصور أو تعدلها وفق وصف محدد.',
 'documents':'تنشئ المستندات وتراجع تنسيقها.', 'pdf':'تقرأ ملفات PDF وتنشئها وتراجع عرضها.',
 'spreadsheets':'تنشئ جداول البيانات وتحللها وتتعامل مع الصيغ.',
 'presentations':'تنشئ العروض التقديمية وتعدلها.',
 'accessibility':'تراجع إمكانية استخدام الواجهة لذوي الاحتياجات المختلفة.',
 'fixing-accessibility':'تعالج مشكلات الوصول في عناصر الواجهة.',
 'web-design-guidelines':'تراجع الواجهات وفق إرشادات تصميم الويب.',
 'skill-creator':'تنشئ مهارات قابلة لإعادة الاستخدام أو تحدثها.',
 'skill-installer':'تساعد في تثبيت المهارات من مصادر محددة.',
 'find-skills':'تبحث عن مهارة مناسبة للمهمة المطلوبة.',
 'mascot-logo':'تصمم تميمة أو شخصية بصرية للمشروع.',
 'brand-kit':'تجمع عناصر الهوية مثل الألوان والخطوط والشعار.',
 'brand-guidelines':'توثق قواعد استخدام الهوية البصرية.',
 'brand-name':'تقترح أسماء مناسبة للعلامة أو المشروع.',
 'llmfit-advisor':'تقيّم ملاءمة نماذج اللغة لمواصفات الجهاز.',
 'write-page':'تنشئ صفحات المحتوى وتعدلها.',
 'write-like-me':'تساعد في صياغة النص بأسلوب المستخدم.',
 'visualize':'تنشئ وسائل بصرية لشرح الأفكار والبيانات.',
 'plugin-management':'تراجع الإضافات واتصالاتها وإدارتها.',
 'template-creator':'تنشئ قوالب قابلة لإعادة الاستخدام.',
 'openai-docs':'تراجع الوثائق الرسمية لتطوير تطبيقات OpenAI.',
 'excel-live-control':'تتعامل مع جلسة Excel المتصلة.',
}

def metadata(text, fallback):
    # Bounded scalar/folded front matter; no YAML object construction or instruction execution.
    lines = text.splitlines()
    data = {}
    if lines and lines[0].strip() == '---':
        key = None
        for line in lines[1:]:
            if line.strip() == '---': break
            match = re.match(r'^(name|description):\s*(.*)$', line)
            if match:
                key, value = match.groups()
                data[key] = '' if value in ('|','>','|-','>-') else value.strip().strip('\"\'')
            elif key and line.startswith((' ', '\t')):
                data[key] += ' ' + line.strip()
            else: key = None
    return (data.get('name') or fallback).strip()[:200], data.get('description','').strip()[:8000]

def explanation(name):
    key = name.lower()
    if key in EXPLANATIONS: return EXPLANATIONS[key], True
    if key.startswith('logo-') or key.endswith('-logo'):
        return 'مهارة متخصصة ضمن تصميم الشعارات؛ يوضح وصف المصدر وظيفتها التفصيلية.', False
    if key.startswith('artifact-template-'):
        return 'قالب لإعداد مستند أو عرض أو جدول؛ راجع وصف المصدر لتحديد مجال القالب.', False
    return 'مهارة مكتشفة؛ الشرح العربي التفصيلي يحتاج مراجعة وصف المصدر.', False

def collect(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir(): raise OSError('Skill root is not a directory')
    found = []
    def fail(error): raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        dirs[:] = [d for d in dirs if not (Path(directory)/d).is_symlink()]
        if 'SKILL.md' not in files: continue
        path = Path(directory)/'SKILL.md'
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_relative_to(root):
            raise OSError('Skill file points outside its scanned location')
        if path.stat().st_size > 1024*1024: raise OSError('Skill definition exceeds scan size limit')
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        name, description = metadata(text, path.parent.name)
        found.append((path.relative_to(root).as_posix(), name, description, hashlib.sha256(raw).hexdigest()))
    return found

@transaction.atomic
def reconcile(alias, found):
    now = timezone.now()
    seen = set()
    for relative, name, description, digest in found:
        seen.add(relative)
        installation = Installation.objects.select_related('version__service').filter(root_alias=alias,relative_path=relative).first()
        if installation:
            service = installation.version.service
        else:
            key = 'skill-location:' + hashlib.sha256((alias+'/'+relative).encode()).hexdigest()[:32]
            service, _ = Service.objects.get_or_create(key=key,defaults={'name':name,'kind':'skill','purpose':description})
        version, _ = ServiceVersion.objects.get_or_create(service=service,sha256=digest,defaults={
            'version_label':digest[:12], 'source':alias+'/'+relative,
            'metadata':{'skill_name':name,'description':description,'fingerprint_scope':'SKILL.md'},
        })
        old = installation.version if installation else None
        action = 'discovered' if not installation else ('restored' if installation.presence=='removed' else ('updated' if old.pk!=version.pk else None))
        if not installation:
            installation = Installation(external_key=hashlib.sha256((alias+'/'+relative).encode()).hexdigest(),root_alias=alias,relative_path=relative)
        if not installation.explanation_reviewed or not installation.explanation:
            installation.explanation, installation.explanation_reviewed = explanation(name)
        installation.source_description = description
        installation.version = version
        installation.presence = 'file_verified'
        installation.observed_at = now
        installation.save()
        if action: SkillChange.objects.create(installation=installation,action=action,old_version=old,new_version=version)
    for installation in Installation.objects.filter(root_alias=alias,presence='file_verified').exclude(relative_path__in=seen):
        installation.presence='removed'
        installation.observed_at=now
        installation.save(update_fields=['presence','observed_at'])
        SkillChange.objects.create(installation=installation,action='removed',old_version=installation.version)
    SkillScan.objects.update_or_create(root_alias=alias,defaults={'scanned_at':now,'succeeded_at':now,'success':True,'files_count':len(found),'error':''})

def scan_skills():
    results = {}
    for alias, root in settings.SKILL_INVENTORY_ROOTS.items():
        try:
            found = collect(root)
            reconcile(alias, found)
            results[alias] = len(found)
        except (OSError, UnicodeError) as exc:
            SkillScan.objects.update_or_create(root_alias=alias,defaults={'scanned_at':timezone.now(),'success':False,'error':str(exc)[:1000]})
            results[alias] = 'failed; previous inventory preserved'
    return results
