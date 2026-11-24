"""نسخ المهام والقرارات من قاعدة المضيف إلى قاعدة النسخة السحابية على Railway (ADR 0010).

الاستعمال من النسخة الرئيسية (فيها قاعدة المضيف):
    python scripts/sync_to_railway.py            # معاينة: الأعداد فقط ولا كتابة
    python scripts/sync_to_railway.py --apply    # تطبيق النسخ

يُصدِّر `dumpdata` من قاعدة المضيف النماذج في MODELS، ويرسلها مضغوطة عبر `railway ssh` إلى
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
MODELS = ['workspace.task', 'workspace.taskupdate', 'workspace.decision', 'registry.registrytask']
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
if any(remote_only.values()) and not FORCE:
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


def dump_local(backend, models=MODELS):
    """نصّ JSON لتصدير النماذج من القاعدة التي يستعملها `manage.py` في backend."""
    env = {**os.environ, 'PYTHONUTF8': '1'}
    result = subprocess.run([sys.executable, 'manage.py', 'dumpdata', *models, '--indent', '0'],
                            cwd=backend, env=env, capture_output=True)
    if result.returncode != 0:
        tail = result.stderr.decode('utf-8', 'replace').splitlines()[-10:]
        sys.exit('فشل التصدير المحلي:\n' + '\n'.join(tail))
    return result.stdout.decode('utf-8')


def local_counts(data):
    counts = {}
    for row in json.loads(data):
        counts[row['model']] = counts.get(row['model'], 0) + 1
    return counts


def build_program(data, apply=False, force=False):
    payload = base64.b64encode(gzip.compress(data.encode('utf-8'))).decode('ascii')
    return (REMOTE_PROGRAM.replace('__PAYLOAD__', repr(payload))
            .replace('__APPLY__', repr(bool(apply))).replace('__FORCE__', repr(bool(force))))


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
    parser.add_argument('--backend', default=str(ROOT / 'backend'),
                        help='مجلد backend الذي فيه قاعدة المضيف (.local/registry.sqlite3)')
    args = parser.parse_args(argv)

    data = dump_local(args.backend)
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
