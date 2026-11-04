"""فحص ما قبل الدفع (مهمة 0002): يعمل من خطّاف pre-push وفي CI بالأمر نفسه.

يفشل بخروج غير صفري إذا فشل أي فحص. لا يطبع سجلات خام؛ عند الفشل آخر 30 سطرًا فقط.
"""
import os
import subprocess
import sys
from pathlib import Path

from guard_frozen import TRAILER, frozen

ROOT = Path(__file__).resolve().parents[1]
TAIL = 30
# رأس الفرع عند قرار التجميد (ADR 0002، 2026-11-03). ما بعده ويمس منطقة مجمّدة يحتاج سببًا مسجلًا،
# لأن revert وcherry-pick و--no-verify لا تمر بخطّاف pre-commit.
FREEZE_BASE = '542b6c0de923372df72d37dad6759a7cbab5ccf9'


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
    ok = True
    private = tracked_private()
    if private:
        ok = False
        print('فشل: ملفات .local متعقَّبة في Git:')
        print('\n'.join(f'  - {p}' for p in private[:20]))
    else:
        print('نجح: لا ملفات .local متعقَّبة')
    unapproved = unapproved_frozen_commits()
    if unapproved is None:
        print('تنبيه: قاعدة التجميد ليست في تاريخ هذا الفرع؛ فحص التاريخ لم يُجرَ')
    elif unapproved:
        ok = False
        print('فشل: التزامات تمس منطقة مجمّدة دون سبب مسجل (ADR 0002 البند 3):')
        print('\n'.join(f'  - {c}' for c in unapproved[:20]))
    else:
        print('نجح: لا التزام يمس منطقة مجمّدة دون سبب مسجل')
    for name, command in checks():
        ok = run(name, command) and ok
    print('فحص ما قبل الدفع: ' + ('ناجح' if ok else 'فاشل — أصلح قبل الدفع'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
