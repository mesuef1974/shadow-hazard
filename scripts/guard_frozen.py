"""حارس التجميد: يرفض التزامًا يمس منطقة مجمّدة بقرار ADR 0002 البند 3.

يعمل من خطّافَي pre-commit وcommit-msg في .githooks/. التجاوز بمتغير بيئة صريح مع سبب،
ويُسجَّل السبب في رسالة الالتزام سطرًا ختاميًا ليبقى أثره في التاريخ.
"""
import os
import subprocess
import sys

from automation_events import record

DECISION = 'ADR 0002 البند 3: المنصة والهوية و docs/blueprint/ مجمّدة حتى تسليم المسابقة.'
FROZEN = ('backend/', 'docs/brand/', 'docs/blueprint/')
OPEN_DIR = 'backend/simulation/'
OPEN_FILE = 'docs/blueprint/prototype-first-experiment-ar.md'
OVERRIDE = 'SHZ_FROZEN_OVERRIDE'
TRAILER = 'Frozen-Override:'
MIN_REASON = 10


def frozen(path):
    path = path.replace('\\', '/')
    return (path.startswith(FROZEN) and not path.startswith(OPEN_DIR)
            and path != OPEN_FILE)


def staged():
    # --no-renames: نقل ملف من منطقة مجمّدة يظهر حذفًا منها فلا يمر خفية.
    out = subprocess.run(['git', 'diff', '--cached', '--name-only', '--no-renames', '-z'],
                         capture_output=True, check=True).stdout
    return [p for p in out.decode('utf-8').split('\0') if p]


def reason():
    return os.environ.get(OVERRIDE, '').strip()


def check(paths):
    blocked = [p for p in paths if frozen(p)]
    if not blocked:
        record('commit', 'pass', f'{len(paths)} ملف')
        return 0
    if len(reason()) >= MIN_REASON:
        print(f'تجاوز التجميد بسبب مصرَّح: {reason()} ({len(blocked)} ملف)', file=sys.stderr)
        record('commit', 'override', reason())
        return 0
    record('commit', 'reject', '، '.join(blocked[:3]))
    print('رُفض الالتزام: تغييرات في منطقة مجمّدة.', file=sys.stderr)
    print(DECISION, file=sys.stderr)
    for path in blocked[:20]:
        print(f'  - {path}', file=sys.stderr)
    if len(blocked) > 20:
        print(f'  ... و{len(blocked) - 20} ملفات أخرى', file=sys.stderr)
    print(f'للتجاوز بقرار موثق من المشرف (سبب من {MIN_REASON} أحرف على الأقل):', file=sys.stderr)
    print(f'  PowerShell: $env:{OVERRIDE}="السبب"; git commit ...; '
          f'Remove-Item Env:{OVERRIDE}', file=sys.stderr)
    print(f'  Bash: {OVERRIDE}="السبب" git commit ...', file=sys.stderr)
    return 1


def add_trailer(message_file):
    """يضيف سبب التجاوز إلى رسالة الالتزام إن كان التجاوز مستخدمًا فعلًا."""
    if not reason() or not any(frozen(p) for p in staged()):
        return 0
    with open(message_file, encoding='utf-8') as handle:
        if TRAILER in handle.read():
            return 0
    # interpret-trailers يحترم سطر القص في `git commit -v` فلا يضيع السبب.
    subprocess.run(['git', 'interpret-trailers', '--in-place', '--trailer',
                    f'{TRAILER} {reason()}', message_file], check=True)
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8', errors='replace')
    if len(argv) == 2 and argv[0] == '--commit-msg':
        return add_trailer(argv[1])
    return check(staged())


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
