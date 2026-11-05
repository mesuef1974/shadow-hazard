"""خلاصة فشل CI (مهمة 0004، المرحلة الأولى العامة): من سجل unittest إلى ≤40 سطرًا.

الوكيل يقرأ هذه الخلاصة لا السجل الخام (ADR 0003 الطبقة 2). حتمي وبلا توكنز.
الاستعمال: python scripts/failure_digest.py <log> [اسم الفحص] [مخرج.md]
"""
import re
import subprocess
import sys

MAX_LINES = 40
MAX_FAILED = 10
FAIL = re.compile(r'^(ERROR|FAIL): (\S+) \(([^)]+)\)')
SUMMARY = re.compile(r'^(Ran \d+ tests? in .+|FAILED \(.*\)|OK.*)$')
EXC = re.compile(r'^[\w.]+(Error|Exception|Failure|Interrupt)\b.*:|^AssertionError')


def parse(text):
    """يرجع (الأخطاء، سطر Ran، سطر FAILED) من نص السجل."""
    failures, ran, failed = [], '', ''
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        match = FAIL.match(line)
        if match:
            current = {'kind': match.group(1), 'test': f'{match.group(3)}', 'error': ''}
            failures.append(current)
            continue
        if line.startswith('Ran '):
            ran = line
        elif line.startswith('FAILED ('):
            failed = line
        if current is not None and not current['error']:
            cleaned = re.sub(r'^\s*', '', line)
            if EXC.match(cleaned):
                current['error'] = cleaned[:160]
    return failures, ran, failed


def changed_files():
    try:
        out = subprocess.run(['git', 'show', '--name-only', '--format=', 'HEAD'],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [name for name in out.splitlines() if name]


def build(text, check='CI', files=None):
    failures, ran, failed = parse(text)
    lines = [f'# خلاصة الفشل — {check}', '']
    if not failures:
        lines += ['لم تُلتقَط اختبارات فاشلة في السجل؛ راجع خطوة الفشل في GitHub Actions.', '']
        tail = [ln for ln in text.splitlines() if ln.strip()][-8:]
        lines += ['آخر أسطر السجل:'] + [f'    {ln[:140]}' for ln in tail]
    else:
        lines += [f'- {ran}' if ran else '- (بلا سطر Ran)', f'- {failed}' if failed else '- (بلا سطر FAILED)', '']
        lines.append(f'الاختبارات الفاشلة ({len(failures)}):')
        for item in failures[:MAX_FAILED]:
            lines.append(f"- {item['kind']} {item['test']}")
            if item['error']:
                lines.append(f"    {item['error']}")
        if len(failures) > MAX_FAILED:
            lines.append(f'- ... و{len(failures) - MAX_FAILED} أخرى')
    if files:
        lines += ['', 'الملفات المتغيرة في آخر إيداع:'] + [f'- {name}' for name in files[:8]]
        if len(files) > 8:
            lines.append(f'- ... و{len(files) - 8} ملفًا')
    return '\n'.join(lines[:MAX_LINES]) + '\n'


def main(argv):
    if not argv:
        print('الاستعمال: failure_digest.py <log> [فحص] [مخرج.md]', file=sys.stderr)
        return 2
    with open(argv[0], encoding='utf-8', errors='replace') as handle:
        text = handle.read()
    digest = build(text, argv[1] if len(argv) > 1 else 'CI', changed_files())
    if len(argv) > 2:
        with open(argv[2], 'w', encoding='utf-8') as handle:
            handle.write(digest)
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(digest, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
