"""Task-specific context and a deliberately narrow, non-eval calculator."""
import hashlib
import os
from pathlib import Path
import re
import tomllib
from decimal import Decimal, localcontext
from django.conf import settings

KINDS=[('auto','تلقائي — حساب مباشر أو نص خفيف'),('text','نص أو سؤال — سياق خفيف'),('calculation','حساب مباشر — دون نموذج'),
       ('code','برمجة أو مراجعة كود'),('design','تصميم واجهة'),('connected','مهمة تحتاج موصلًا محددًا')]
SKILLS={
    'minimal-code-edit':('.codex/skills/minimal-code-edit/SKILL.md','تعديل برمجي محدود'),
    'focused-testing':('.codex/skills/focused-testing/SKILL.md','اختبارات مركزة'),
    'frontend-design':('.agents/skills/frontend-design/SKILL.md','تصميم الواجهة'),
    'ui-ux-pro-max':('.agents/skills/ui-ux-pro-max/SKILL.md','مراجعة تجربة الاستخدام'),
}
DEFAULT_SKILLS={'code':['minimal-code-edit'],'design':['frontend-design']}
EXPRESSION=re.compile(r'\s*(?:احسب\s+)?([+-]?\d{1,12}(?:\.\d{1,8})?)\s*([+*/−-])\s*([+-]?\d{1,12}(?:\.\d{1,8})?)\s*')


def host_config():
    path=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'config.toml'
    return tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def available_plugins():
    return ['web-search',*sorted(host_config().get('plugins',{}))]


def instruction_files():
    root=settings.BASE_DIR.parent
    paths=[]
    for directory in [Path(os.environ.get('CODEX_HOME',Path.home()/'.codex')),*reversed([root,*root.parents])]:
        path=directory/'AGENTS.override.md'
        if not path.is_file():path=directory/'AGENTS.md'
        if path.is_file():paths.append(path)
    return paths


def calculate(expression):
    match=EXPRESSION.fullmatch(expression)
    if not match:raise ValueError('الحساب المحلي يقبل عملية واحدة فقط، مثل 17 + 25؛ لا يقبل تعليمات نصية أو أكواد.')
    a,operator,b=match.groups();a=Decimal(a);b=Decimal(b)
    with localcontext() as context:
        context.prec=40
        if operator in ('-','−'):result=a-b
        elif operator=='+':result=a+b
        elif operator=='*':result=a*b
        else:
            if b==0:raise ValueError('لا يمكن القسمة على صفر.')
            result=a/b
    if result==0:return '0'
    return format(result,'f').rstrip('0').rstrip('.') if '.' in format(result,'f') else format(result,'f')


def build_plan(charter,prompt,kind='auto',skills=None,plugins=None,mode='read-only'):
    if kind not in dict(KINDS):raise ValueError('نوع مهمة غير صالح')
    if kind=='auto':
        text=prompt.casefold()
        kind=('calculation' if EXPRESSION.fullmatch(prompt) else
            'design' if any(word in text for word in ('تصميم','واجهة','css','layout','ui design')) else
            'code' if any(word in text for word in ('باك إند','باك اند','backend','django','python','برمج','كود','هجرة','قاعدة البيانات')) else 'text')
    skills=list(dict.fromkeys([*DEFAULT_SKILLS.get(kind,[]),*(skills or [])]))
    plugins=list(dict.fromkeys(plugins or []))
    if len(skills)>3 or any(key not in SKILLS for key in skills):raise ValueError('مهارة غير مسموحة')
    if len(plugins)>2 or any(name not in available_plugins() for name in plugins):raise ValueError('موصل غير مسجل')
    if plugins and kind!='connected':raise ValueError('اختر نوع المهمة التي تحتاج موصلًا قبل تفعيله')
    if kind=='connected' and not plugins:raise ValueError('حدد الموصل الذي تحتاجه المهمة')
    if kind=='calculation':
        if skills or plugins or mode!='read-only':raise ValueError('الحساب المحلي لا يحتاج مهارات أو موصلات أو صلاحية تعديل')
        calculate(prompt)
    from workspace.memory import instructions as project_instructions
    memory_text,memory_digest=project_instructions(charter.project)
    instructions=[memory_text] if memory_text else []
    manifest={'files':[],'local_only':kind=='calculation','project_memory_digest':memory_digest}
    # Retain applicable project/ancestor instructions without truncation.
    paths=instruction_files()
    manifest['instruction_paths']=[str(path) for path in paths]
    for path in paths:
        text=path.read_text(encoding='utf-8-sig')
        instructions.append(text)
        manifest['files'].append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    skill_text=[]
    for key in skills:
        path=Path.home()/SKILLS[key][0]
        if not path.is_file():raise ValueError('تعليمات المهارة المطلوبة غير متاحة: '+key)
        text=path.read_text(encoding='utf-8-sig')
        if len(text)>100000:raise ValueError('المهارة كبيرة؛ يلزم تقسيم المهمة بدل اقتطاع التعليمات')
        skill_text.append(f'مهارة مطلوبة: {key}\nالمصدر: {path}\n{text}')
        manifest['files'].append({'path':str(path),'skill':key,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'kind':kind,'skills':skills,'plugins':plugins,'instructions':'\n\n'.join(instructions),
        'charter':f'نطاق الجلسة: {charter.scope}\nالميثاق: {charter.rules}',
        'skill_text':'\n\n'.join(skill_text),'manifest':manifest}


def capability_flags(plugins):
    config=host_config()
    unknown=set(plugins)-set(config.get('plugins',{}))-{'web-search'}
    if unknown:raise ValueError('الموصل المطلوب لم يعد مسجلًا')
    flags=['--disable','memories','--enable','skip_host_skill_discovery',
           '--enable','omit_skill_descriptions','--disable','plugin_discovery_prefetch',
           '--enable' if set(plugins)-{'web-search'} else '--disable','plugins','--disable','apps',
           '-c','web_search="live"' if 'web-search' in plugins else 'web_search="disabled"']
    for name in config.get('plugins',{}):
        if not re.fullmatch(r'[A-Za-z0-9_@-]+',name):raise ValueError('اسم موصل غير مدعوم في إعداد التنفيذ')
        flags+=['-c',f'plugins.{name}.enabled={str(name in plugins).lower()}']
    for name in config.get('mcp_servers',{}):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',name):raise ValueError('اسم خادم موصل غير مدعوم')
        flags+=['-c',f'mcp_servers.{name}.enabled=false']
    return flags


def validate_manifest(plan):
    if 'project_memory_digest' in plan.manifest:
        from workspace.memory import instructions
        if instructions(plan.job.charter.project)[1]!=plan.manifest['project_memory_digest']:
            raise ValueError('تغيرت ذاكرة المشروع؛ يلزم تحديث سياق المهمة')
    if plan.manifest.get('instruction_paths') is not None and plan.manifest['instruction_paths']!=[str(path) for path in instruction_files()]:
        raise ValueError('تغيرت ملفات تعليمات المشروع؛ أنشئ طلبًا جديدًا')
    for entry in plan.manifest.get('files',[]):
        path=Path(entry['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
            raise ValueError('تغيرت تعليمات السياق بعد التفويض؛ أنشئ طلبًا جديدًا بالتعليمات الحالية')
