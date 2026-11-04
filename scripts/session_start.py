"""خطّاف SessionStart لـClaude Code: «حالة الكود» من ملف التسليم والمهام المفتوحة فقط.

بحد 15 سطرًا. لا يوقف الجلسة أبدًا: أي خطأ يُطبع سطرًا واحدًا ويخرج بصفر.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 15
SECTION = '## حالة الكود'
DONE = ('منجزة', 'مكتملة', 'مغلقة')


def handoff(root):
    files = sorted((root / 'docs').glob('handoff-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md'))
    return files[-1] if files else None


def code_status(path):
    lines, inside = [], False
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if line.startswith('## '):
            if inside:
                break
            inside = line.strip() == SECTION
        elif inside and line.strip():
            lines.append(line.rstrip())
    return lines


def open_tasks(root):
    rows = []
    for path in sorted((root / 'docs' / 'tasks').glob('[0-9][0-9][0-9][0-9]-*.md')):
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        title = lines[0].lstrip('#').strip() if lines else path.stem
        status = next((line.split(':', 1)[1].strip() for line in lines
                       if line.startswith('- الحالة:')), 'غير محددة')
        if not status.startswith(DONE):
            rows.append(f'- {title} — {status.split("؛")[0]}')
    return rows


def hooks_active(root):
    try:
        out = subprocess.run(['git', 'config', '--get', 'core.hooksPath'], cwd=root,
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True  # لا Git متاح: لا تنبيه مضلل.
    return out.replace('\\', '/').rstrip('/') == '.githooks'


def render(root=ROOT):
    path = handoff(root)
    status = code_status(path) if path else ['- لا يوجد ملف تسليم في docs/.']
    tasks = open_tasks(root) or ['- لا مهام مفتوحة.']
    head = [f'حالة الكود ({path.relative_to(root).as_posix() if path else "docs/"}):']
    tail = ['المهام المفتوحة (docs/tasks/):'] + tasks
    if not hooks_active(root):
        tail.append('تنبيه: حرّاس Git غير مفعّلين؛ شغّل مرة واحدة: python scripts/install_hooks.py')
    room = LIMIT - len(head) - len(tail)
    if room < 2:  # مهام كثيرة: سطر واحد للحالة على الأقل، وعلامة على كل قطع.
        tail = tail[:LIMIT - len(head) - 3] + ['- … البقية في docs/tasks/.']
        return head + status[:1] + ['- … البقية في ملف التسليم.'] + tail
    if len(status) > room:
        status = status[:room - 1] + ['- … البقية في ملف التسليم.']
    return head + status + tail


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    try:
        print('\n'.join(render()))
    except Exception as error:  # الخطّاف لا يمنع بدء الجلسة.
        print(f'تعذر قراءة حالة الكود: {error}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
