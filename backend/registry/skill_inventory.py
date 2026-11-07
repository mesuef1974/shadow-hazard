"""Observe SKILL.md files as data only; never import or execute skill code."""
import hashlib
import json
import stat
import os
import re
import logging
import time
from pathlib import Path
from django.conf import settings
from django.db import connection, transaction, OperationalError
from django.utils import timezone
from .models import Installation, Service, ServiceVersion, SkillChange, SkillScan, SkillBlob, SkillFileSnapshot, SkillFileChange

EXPLANATIONS = {
 'review-agent':'تراجع التغيير من ناحية العيوب المحتملة دون تعديل الملفات.',
 'code-simplification':'تبسط الشفرة عندما توجد فائدة واضحة، وتحافظ على السلوك والواجهات.',
 'code-review-and-quality':'تفحص التغيير للصحة والوضوح والحدود والأمان والأداء.',
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

def is_link(path):
    return path.is_symlink() or path.is_junction()

def bundle(directory):
    """Snapshot all descendants including hidden/binary files; links are data, not traversal."""
    files = []
    total = 0
    limit = getattr(settings, 'SKILL_BUNDLE_MAX_BYTES', 256 * 1024 * 1024)
    def fail(error): raise error
    for current, dirs, names in os.walk(directory, followlinks=False, onerror=fail):
        for name in list(dirs):
            if is_link(Path(current)/name):
                dirs.remove(name)
                names.append(name)
        for name in sorted(names):
            path = Path(current)/name
            before = path.lstat()
            kind = 'link' if is_link(path) else 'file'
            if kind == 'link':
                data = os.readlink(path).encode('utf-8')
            else:
                if not stat.S_ISREG(before.st_mode): raise OSError('Unsupported special file: '+str(path))
                if not path.resolve(strict=True).is_relative_to(directory): raise OSError('File escaped skill directory')
                if before.st_size + total > limit: raise OSError('Skill bundle size limit exceeded: '+str(directory))
                with path.open('rb') as stream:
                    # الحجم الفعلي لا السقف: read(n) تحجز n بايت مسبقًا، فكان كل ملف يحجز نحو 256 MB.
                    data = stream.read(min(before.st_size, limit - total) + 1)
            after = path.lstat()
            if (before.st_mtime_ns,before.st_size,before.st_ino) != (after.st_mtime_ns,after.st_size,after.st_ino):
                raise OSError('File changed during snapshot: '+str(path))
            total += len(data)
            if total > limit: raise OSError('Skill bundle size limit exceeded')
            files.append({'path':path.relative_to(directory).as_posix(),'kind':kind,'sha256':hashlib.sha256(data).hexdigest(),'data':data})
    files.sort(key=lambda f:f['path'])
    manifest=[{k:f[k] for k in ('path','kind','sha256')} for f in files]
    digest=hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return digest,files

def collect(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir(): raise OSError('Skill root is not a directory')
    found = []
    def fail(error): raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        dirs[:] = [d for d in dirs if not is_link(Path(directory)/d)]
        if 'SKILL.md' not in files: continue
        path = Path(directory)/'SKILL.md'
        if is_link(path): raise OSError('Skill definition must be a regular file')
        digest, members = bundle(path.parent.resolve())
        definition = next(f for f in members if f['path']=='SKILL.md')
        text = definition['data'].decode('utf-8-sig')
        name, description = metadata(text, path.parent.name)
        found.append((path.relative_to(root).as_posix(),name,description,digest,members))
    return found

def save_files(version, members):
    for member in members:
        blob, _ = SkillBlob.objects.get_or_create(sha256=member['sha256'],defaults={'content':member['data'],'size':len(member['data'])})
        SkillFileSnapshot.objects.get_or_create(version=version,path=member['path'],defaults={'kind':member['kind'],'blob':blob})

def file_changes(change, old, new, baseline=False):
    previous = {f.path:f for f in SkillFileSnapshot.objects.filter(version=old)} if old else {}
    current = {f.path:f for f in SkillFileSnapshot.objects.filter(version=new)} if new else {}
    for path in sorted(previous.keys() | current.keys()):
        before, after = previous.get(path), current.get(path)
        if before and after and (before.blob_id,before.kind)==(after.blob_id,after.kind): continue
        action = 'baseline' if baseline else ('added' if not before else ('removed' if not after else 'modified'))
        SkillFileChange.objects.create(change=change,path=path,action=action,old_blob_id=before.blob_id if before else None,new_blob_id=after.blob_id if after else None)

@transaction.atomic
def _reconcile(alias, found):
    if connection.vendor == 'sqlite':
        # Reserve the writer before reads: a deferred read -> write upgrade can
        # fail immediately under contention despite SQLite's busy timeout.
        table = connection.ops.quote_name(SkillScan._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f'UPDATE {table} SET root_alias = root_alias WHERE 1 = 0')
    now = timezone.now()
    seen = set()
    for relative, name, description, digest, members in found:
        seen.add(relative)
        installation = Installation.objects.select_related('version__service').filter(root_alias=alias,relative_path=relative).first()
        if installation:
            service = installation.version.service
        else:
            key = 'skill-location:' + hashlib.sha256((alias+'/'+relative).encode()).hexdigest()[:32]
            service, _ = Service.objects.get_or_create(key=key,defaults={'name':name,'kind':'skill','purpose':description})
        version, new_version = ServiceVersion.objects.get_or_create(service=service,sha256=digest,defaults={
            'version_label':digest[:12], 'source':alias+'/'+relative,
            'metadata':{'skill_name':name,'description':description,'fingerprint_scope':'skill-directory-v1','files_count':len(members)},
        })
        if new_version or SkillFileSnapshot.objects.filter(version=version).count() != len(members):
            save_files(version, members)
        old = installation.version if installation else None
        baseline = bool(old and old.metadata.get("fingerprint_scope") != "skill-directory-v1")
        action = 'discovered' if not installation else ('restored' if installation.presence=='removed' else ('updated' if old.pk!=version.pk else None))
        if baseline and action == 'updated': action = 'baseline'
        if not installation:
            installation = Installation(external_key=hashlib.sha256((alias+'/'+relative).encode()).hexdigest(),root_alias=alias,relative_path=relative)
        if not installation.explanation_reviewed or not installation.explanation:
            installation.explanation, installation.explanation_reviewed = explanation(name)
        installation.source_description = description
        installation.version = version
        installation.presence = 'file_verified'
        installation.observed_at = now
        installation.save()
        if action:
            change = SkillChange.objects.create(installation=installation,action=action,old_version=old,new_version=version)
            file_changes(change, None if action=='restored' else old, version, baseline=baseline)
    for installation in Installation.objects.filter(root_alias=alias,presence='file_verified').exclude(relative_path__in=seen):
        installation.presence='removed'
        installation.observed_at=now
        installation.save(update_fields=['presence','observed_at'])
        change=SkillChange.objects.create(installation=installation,action='removed',old_version=installation.version)
        file_changes(change,installation.version,None)
    SkillScan.objects.update_or_create(root_alias=alias,defaults={'scanned_at':now,'succeeded_at':now,'success':True,'files_count':len(found),'error':''})


def sqlite_busy(exc):
    code = getattr(exc.__cause__, 'sqlite_errorcode', None)
    return connection.vendor == 'sqlite' and (
        (code is not None and code & 255 in (5, 6))
        or str(exc).lower() in ('database is locked', 'database table is locked')
    )


def reconcile(alias, found):
    for attempt in range(3):
        try:
            return _reconcile(alias, found)
        except OperationalError as exc:
            if not sqlite_busy(exc) or attempt == 2:
                raise
            # _reconcile has rolled back before we wait or retry.
            time.sleep(0.1 * (attempt + 1))

def scan_skills():
    results = {}
    for alias, root in settings.SKILL_INVENTORY_ROOTS.items():
        try:
            found = collect(root)
            reconcile(alias, found)
            results[alias] = len(found)
        except OperationalError as exc:
            if not sqlite_busy(exc):
                raise
            logging.getLogger(__name__).warning(
                'Skill inventory deferred for %s: database busy; previous inventory preserved', alias,
            )
            results[alias] = 'deferred; database busy; previous inventory preserved'
        except (OSError, UnicodeError) as exc:
            SkillScan.objects.update_or_create(root_alias=alias,defaults={'scanned_at':timezone.now(),'success':False,'error':str(exc)[:1000]})
            results[alias] = 'failed; previous inventory preserved'
    return results
