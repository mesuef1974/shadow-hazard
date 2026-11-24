"""نسخ المهام والقرارات من قاعدة المضيف إلى قاعدة النسخة السحابية على Railway (ADR 0010).

الاستعمال (من النسخة الرئيسية أو من شجرة عمل؛ القاعدة تُكتشف في النسخة الرئيسية):
    python scripts/sync_to_railway.py            # معاينة: الأعداد فقط ولا كتابة
    python scripts/sync_to_railway.py --apply    # تطبيق النسخ

يُصدِّر من قاعدة المضيف النماذج في MODELS مع آبائها في REFERENCED، ويرسلها مضغوطة عبر `railway ssh` إلى
`manage.py shell` في الخدمة، فيُحمَّلها `loaddata` ويُعاد ضبط تسلسل المفاتيح. الصفوف تُطابَق
بالمفتاح الأساسي: ما يوجد في الطرفين بالرقم نفسه تغلبه نسخة المضيف. لذلك يُرفض التطبيق إذا وُجدت
في Railway صفوف غير موجودة في المضيف (أُنشئت هناك)، إلا مع --force. المستخدمون لا يُنسخون: يجب
أن تتطابق أرقامهم في القاعدتين (علاقات المسؤول والمنشئ والكاتب).

إذن `railway ssh` يمنحه المالك لكل عمل. المتغيران RAILWAY_CLI وRAILWAY_SERVICE يغلبان القيمتين
الافتراضيتين.
"""
import argparse
import base64
import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# تُنسخ كاملة (المطابقة بالمفتاح الأساسي).
MODELS = [
    'workspace.task', 'workspace.taskupdate', 'workspace.decision', 'registry.registrytask',
    'knowledge.knowledgesource', 'knowledge.knowledgechunk',
    'workspace.projectdocumentation', 'workspace.projectmemory',
    'releases.release', 'releases.event', 'releases.evidence',
    'registry.branddirection', 'registry.brandcolor', 'registry.brandasset',
    'registry.brandtypography',
]
# تُنسخ منها الصفوف التي تشير إليها صفوف MODELS وحدها (أصول الهوية وإصدارات الخطوط).
REFERENCED = ['registry.artifact', 'registry.service', 'registry.serviceversion']
# موجودة في السحابة بالأرقام نفسها ولا تُنسخ (المستخدمون والمشروع ومراحله).
ASSUMED = ['accounts.user', 'workspace.project', 'workspace.phase']
# تُنسخ آخر N صفًّا بالمفتاح الأساسي فقط (نافذة منزلقة): تشغيلات المحاكاة كلها 406 م.ب لأن نتيجة
# التشغيل الواحد نحو 750 ك.ب. لا يُرفض النسخ بسبب صفوف أقدم في السحابة خرجت من النافذة.
LATEST = {'simulation.simulationrun': 20}
# لا تُنسخ عمدًا: سجلات الأتمتة والاستهلاك الداخلية. أي مفتاح أجنبي يشير إلى ما سواها يُفرَّغ إن قبل null وإلا يفشل التصدير.
SERVICE = '89aa63da-c7d2-4b68-a141-7d83942a532b'
REMOTE_BACKEND = '/app/backend'

# يُنفَّذ داخل `manage.py shell` في Railway ويقرأ من stdin؛ لا اقتباس في سطر الأمر.
REMOTE_PROGRAM = '''
import base64, gzip, json, os, tempfile
from django.apps import apps
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connection

PAYLOAD = __PAYLOAD__
SLIDING = __SLIDING__
APPLY = __APPLY__
FORCE = __FORCE__

rows = json.loads(gzip.decompress(base64.b64decode(PAYLOAD)))
models = {row['model']: apps.get_model(row['model']) for row in rows}
incoming = {}
for row in rows:
    pk = models[row['model']]._meta.pk
    incoming.setdefault(row['model'], set()).add(pk.to_python(row['pk']))
remote_only = {}
for label, model in models.items():
    have = set(model.objects.values_list('pk', flat=True))
    remote_only[label] = sorted(have - incoming[label], key=str)
    print('PLAN', label, 'export', len(incoming[label]), 'remote', len(have),
          'remote_only', len(remote_only[label]))
blocking = {label: ids for label, ids in remote_only.items() if label not in SLIDING and ids}
if blocking and not FORCE:
    print('RESULT blocked')
elif not APPLY:
    print('RESULT dry')
else:
    fd, path = tempfile.mkstemp(suffix='.json')
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(rows, handle, ensure_ascii=False)
    call_command('loaddata', path, verbosity=0)
    os.unlink(path)
    statements = connection.ops.sequence_reset_sql(no_style(), list(models.values()))
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    for label, model in models.items():
        print('COUNT', label, model.objects.count())
    print('RESULT applied')
'''


def has_host_db(backend):
    db = Path(backend) / '.local' / 'registry.sqlite3'
    return db.is_file() and db.stat().st_size > 1_000_000


def default_backend(root=ROOT):
    """backend الذي فيه قاعدة المضيف: هذه النسخة، وإلا النسخة الرئيسية (شجرة العمل بلا قاعدة)."""
    own = Path(root) / 'backend'
    if has_host_db(own):
        return own
    common = subprocess.run(['git', 'rev-parse', '--git-common-dir'], cwd=root,
                            capture_output=True)
    if common.returncode == 0:
        git_dir = common.stdout.decode('utf-8').strip()
        main = (Path(root) / git_dir).resolve().parent / 'backend'
        if has_host_db(main):
            return main
    sys.exit('لم أجد قاعدة المضيف (backend/.local/registry.sqlite3)؛ حدّد --backend.')

LOCAL_PROGRAM = '''
import json, sys
from django.apps import apps
from django.core import serializers

FULL = __FULL__
LATEST = __LATEST__
REFERENCED = __REFERENCED__
ASSUMED = __ASSUMED__
copied = {}
for label in FULL:
    model = apps.get_model(label)
    copied[model] = {obj.pk: obj for obj in model.objects.all()}
for label, count in LATEST.items():
    model = apps.get_model(label)
    copied[model] = {obj.pk: obj for obj in model.objects.order_by('-pk')[:count]}
ref_models = {apps.get_model(label) for label in REFERENCED}
for model in ref_models:
    copied[model] = {}
assumed = {apps.get_model(label) for label in ASSUMED}
nulled = {}
queue = [obj for objs in list(copied.values()) for obj in objs.values()]
while queue:
    obj = queue.pop()
    for field in obj._meta.concrete_fields:
        if not field.is_relation or getattr(obj, field.attname) is None:
            continue
        target = field.related_model
        if target in assumed:
            continue
        if target in copied:
            if target in ref_models and getattr(obj, field.attname) not in copied[target]:
                parent = target.objects.get(pk=getattr(obj, field.attname))
                copied[target][parent.pk] = parent
                queue.append(parent)
        elif field.null:
            key = obj._meta.label_lower + '.' + field.name
            nulled[key] = nulled.get(key, 0) + 1
            setattr(obj, field.attname, None)
        else:
            name = obj._meta.label_lower + '.' + field.name
            sys.exit('FKERR ' + name + ' -> ' + target._meta.label_lower)
rows = [obj for objs in copied.values() for obj in objs.values()]
for key, count in sorted(nulled.items()):
    print('NULLED', key, count)
print('FIXTURE' + serializers.serialize('json', rows))
'''


def local_program():
    program = LOCAL_PROGRAM.replace('__FULL__', repr(MODELS))
    program = program.replace('__LATEST__', repr(LATEST))
    program = program.replace('__REFERENCED__', repr(REFERENCED))
    return program.replace('__ASSUMED__', repr(ASSUMED))


def dump_local(backend):
    """نصّ JSON لتصدير النماذج (وآبائها المشار إليهم) من القاعدة التي يستعملها `manage.py`."""
    env = {**os.environ, 'PYTHONUTF8': '1'}
    result = subprocess.run([sys.executable, 'manage.py', 'shell', '-c', local_program()],
                            cwd=backend, env=env, capture_output=True)
    out = result.stdout.decode('utf-8', 'replace').splitlines()
    for line in out:
        if line.startswith('NULLED '):
            print('فُرِّغ (مفتاح أجنبي إلى جدول غير منسوخ):', line[7:])
    fixture = [line[len('FIXTURE'):] for line in out if line.startswith('FIXTURE')]
    if result.returncode != 0 or not fixture:
        tail = (result.stderr.decode('utf-8', 'replace').splitlines()[-10:]
                + [line for line in out if line.startswith('FKERR')])
        sys.exit('فشل التصدير المحلي:\n' + '\n'.join(tail))
    return fixture[0]


def local_counts(data):
    counts = {}
    for row in json.loads(data):
        counts[row['model']] = counts.get(row['model'], 0) + 1
    return counts


def build_program(data, apply=False, force=False):
    payload = base64.b64encode(gzip.compress(data.encode('utf-8'))).decode('ascii')
    program = REMOTE_PROGRAM.replace('__PAYLOAD__', repr(payload))
    program = program.replace('__SLIDING__', repr(sorted(LATEST)))
    program = program.replace('__APPLY__', repr(bool(apply)))
    return program.replace('__FORCE__', repr(bool(force)))


def parse_result(output):
    """(النتيجة أو None، أسطر PLAN وCOUNT) من مخرجات الطرف البعيد."""
    result, lines = None, []
    for line in output.splitlines():
        if line.startswith('RESULT '):
            result = line.split(' ', 1)[1].strip()
        elif line.startswith(('PLAN ', 'COUNT ')):
            lines.append(line)
    return result, lines


def railway_cli():
    path = os.environ.get('RAILWAY_CLI') or shutil.which('railway')
    if not path and os.environ.get('APPDATA'):
        candidate = Path(os.environ['APPDATA']) / 'npm/node_modules/@railway/cli/bin/railway.exe'
        path = str(candidate) if candidate.exists() else None
    if not path:
        sys.exit('لم أجد railway CLI؛ حدّد RAILWAY_CLI بمسارها الكامل.')
    return path


def run_remote(program, service):
    command = [railway_cli(), 'ssh', '--service', service, '--', 'sh', '-c',
               f'cd {REMOTE_BACKEND} && python manage.py shell']
    result = subprocess.run(command, input=program.encode('utf-8'), capture_output=True)
    return result.returncode, result.stdout.decode('utf-8', 'replace') + \
        result.stderr.decode('utf-8', 'replace')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--apply', action='store_true', help='نفّذ النسخ (الافتراضي معاينة)')
    parser.add_argument('--force', action='store_true',
                        help='اسمح بوجود صفوف في Railway غير موجودة في المضيف')
    parser.add_argument('--backend',
                        help='مجلد backend الذي فيه قاعدة المضيف؛ الافتراضي يُكتشف تلقائيًّا')
    args = parser.parse_args(argv)

    backend = args.backend or default_backend()
    print('قاعدة المضيف من:', backend)
    data = dump_local(backend)
    print('المضيف:', ', '.join(f'{k} {v}' for k, v in sorted(local_counts(data).items())))
    code, output = run_remote(build_program(data, args.apply, args.force),
                              os.environ.get('RAILWAY_SERVICE', SERVICE))
    result, lines = parse_result(output)
    print('\n'.join(lines))
    if result is None:
        print('\n'.join(output.splitlines()[-15:]))
        sys.exit(f'لم تصل نتيجة من Railway (خروج {code}).')
    messages = {'dry': 'معاينة فقط؛ أضف --apply للنسخ.', 'applied': 'تم النسخ.',
                'blocked': 'مرفوض: في Railway صفوف ليست في المضيف؛ راجعها أو استعمل --force.'}
    print(messages.get(result, result))
    sys.exit(1 if result == 'blocked' else 0)


if __name__ == '__main__':
    main()
