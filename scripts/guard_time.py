"""حارس الاختبارات المرتبطة بالساعة (أتمتة جذرية بعد حادثة token_usage).

يفحص الأسطر المضافة فقط (فرق عن القاعدة) في ملفات الاختبار: استعمال timezone.now() أو
datetime.now() أو time.time() بلا تثبيت يجعل الاختبار يفشل عند حدّ الدقيقة أو الساعة أو اليوم.
مسموح السطر إن ثبّت الوقت (replace( أو freeze أو patch أو mock) أو حمل الوسم  # زمن-ثابت.
"""
import re
import subprocess
import sys

CLOCK = re.compile(r'\b(timezone\.now|datetime\.(?:utc)?now|time\.time)\s*\(')
SAFE = re.compile(r'replace\(|freeze|patch|mock|# زمن-ثابت')
TEST_FILE = re.compile(r'(^|/)(tests?\.py|test_[^/]*\.py|[^/]*_tests?\.py|tests/[^/]+\.py)$')


def scan_diff(diff_text):
    """يرجع [(ملف، سطر)] للأسطر المضافة المشبوهة في ملفات الاختبار."""
    found, current = [], None
    for line in diff_text.splitlines():
        if line.startswith('+++ '):
            path = line[4:].removeprefix('b/')
            current = path if TEST_FILE.search(path) else None
        elif current and line.startswith('+') and not line.startswith('+++'):
            body = line[1:]
            if CLOCK.search(body) and not SAFE.search(body):
                found.append((current, body.strip()[:120]))
    return found


def run_git(root, *args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)


def base_ref(root='.'):
    """نقطة بدء الفحص: في طلب CI فرع القاعدة، وعند الدفع أول التزام لم يُدفع بعد.

    لا نفحص تاريخًا مدفوعًا سابقًا (لا نحاسب شيفرة قديمة). None إن لم يوجد ما يُفحص.
    """
    import os
    target = os.environ.get('GITHUB_BASE_REF')
    if target:
        out = run_git(root, 'merge-base', 'HEAD', f'origin/{target}')
        return out.stdout.strip() or None if out.returncode == 0 else None
    out = run_git(root, 'rev-list', '--reverse', 'HEAD', '--not', '--remotes')
    commits = out.stdout.split() if out.returncode == 0 else []
    if not commits:
        return None
    parent = run_git(root, 'rev-parse', f'{commits[0]}^')
    return parent.stdout.strip() if parent.returncode == 0 else None


def flagged(root='.'):
    base = base_ref(root)
    if base is None:
        return []
    diff = subprocess.run(['git', 'diff', '-U0', '--no-renames', f'{base}..HEAD', '--', '*.py'],
                          cwd=root, capture_output=True, text=True, encoding='utf-8',
                          errors='replace').stdout
    return scan_diff(diff)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    found = flagged()
    for path, text in found:
        print(f'  - {path}: {text}')
    if found:
        print('ثبّت الوقت (replace(minute=30,...) أو patch) أو ضع الوسم  # زمن-ثابت  إن كان متعمدًا.')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
