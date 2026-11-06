"""فحص ما قبل الدفع (مهمة 0002): يعمل من خطّاف pre-push وفي CI بالأمر نفسه.

يفشل بخروج غير صفري إذا فشل أي فحص. لا يطبع سجلات خام؛ عند الفشل آخر 30 سطرًا فقط.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from automation_events import record
from guard_frozen import TRAILER, frozen

ROOT = Path(__file__).resolve().parents[1]
TAIL = 30
# قاعدة التجميد على الفرع الرئيسي: دمج طلب #2 (شجرته مطابقة لرأس الفرع عند قرار ADR 0002 في
# 2026-11-03). ما بعدها ويمس منطقة مجمّدة يحتاج سببًا مسجلًا، لأن revert وcherry-pick و--no-verify
# لا تمر بخطّاف pre-commit. وجودها على main يبقيها في التاريخ مهما كانت طريقة دمج الفروع اللاحقة.
FREEZE_BASE = '8bb38489f1b138cbbcc916243eb2cf329f8c778d'


def tracked_private(root=ROOT):
    """مسارات متعقَّبة داخل أي مجلد .local (خاص بالمضيف ولا يدخل Git)."""
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True,
                         check=True).stdout
    return [p for p in out.decode('utf-8').split('\0') if p and '.local' in Path(p).parts]


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, check=True).stdout


def unapproved_frozen_commits(root=ROOT, base=FREEZE_BASE):
    """التزامات بعد التجميد تمس منطقة مجمّدة دون سطر Frozen-Override. None إن غابت القاعدة."""
    if subprocess.run(['git', 'merge-base', '--is-ancestor', base, 'HEAD'], cwd=root,
                      capture_output=True).returncode != 0:
        return None
    found = []
    for commit in git(root, 'rev-list', f'{base}..HEAD').decode().split():
        files = git(root, 'diff-tree', '--no-commit-id', '--name-only', '-r', '--no-renames',
                    '-z', commit).decode('utf-8').split('\0')
        if any(frozen(f) for f in files if f) and TRAILER not in git(
                root, 'log', '-1', '--format=%B', commit).decode('utf-8'):
            found.append(git(root, 'log', '-1', '--format=%h %s', commit).decode('utf-8').strip())
    return found


def checks():
    python = sys.executable
    return [
        ('تباين الهوية في الوضعين', [python, 'scripts/check_contrast.py']),
        ('الاختبارات المرتبطة بالساعة', [python, 'scripts/guard_time.py']),
        ('اختبارات الحرّاس', [python, '-m', 'unittest', 'discover', '-s', 'scripts',
                              '-p', 'test_*.py', '-q']),
        ('اختبارات المحاكاة', [python, 'backend/manage.py', 'test', 'simulation', '--noinput']),
    ]


def run(name, command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', errors='replace',
                            env={**os.environ, 'PYTHONUTF8': '1'})
    if result.returncode == 0:
        print(f'نجح: {name}')
        return True
    print(f'فشل: {name}')
    output = (result.stdout + result.stderr).splitlines()
    print('\n'.join(output[-TAIL:]))
    return False


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8', errors='replace')
    ok, started, failed = True, time.monotonic(), []
    private = tracked_private()
    if private:
        ok = False
        print('فشل: ملفات .local متعقَّبة في Git:')
        print('\n'.join(f'  - {p}' for p in private[:20]))
    else:
        print('نجح: لا ملفات .local متعقَّبة')
    unapproved = unapproved_frozen_commits()
    if unapproved is None and os.environ.get('GITHUB_ACTIONS') == 'true':
        ok = False  # في CI لا يُسمح بتخطٍّ صامت: التاريخ كامل (fetch-depth: 0) والقاعدة على main.
        print('فشل: قاعدة التجميد ليست في تاريخ هذا الفرع؛ ادمج main فيه أولًا')
    elif unapproved is None:
        print('تنبيه: قاعدة التجميد ليست في تاريخ هذا الفرع؛ فحص التاريخ لم يُجرَ')
    elif unapproved:
        ok = False
        print('فشل: التزامات تمس منطقة مجمّدة دون سبب مسجل (ADR 0002 البند 3):')
        print('\n'.join(f'  - {c}' for c in unapproved[:20]))
    else:
        print('نجح: لا التزام يمس منطقة مجمّدة دون سبب مسجل')
    for name, command in checks():
        passed = run(name, command)
        ok = passed and ok
        if not passed:
            failed.append(name)
    print('فحص ما قبل الدفع: ' + ('ناجح' if ok else 'فاشل — أصلح قبل الدفع'))
    record('push', 'pass' if ok else 'fail',
           f'{round(time.monotonic() - started)} ث' + (' · ' + '، '.join(failed) if failed else ''),
           ROOT)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
