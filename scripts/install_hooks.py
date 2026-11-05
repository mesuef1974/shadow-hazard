"""تفعيل حرّاس Git لكل شجرة عمل: core.hooksPath = .githooks (نسبيّ، فيُحلّ داخل الشجرة نفسها).

السبب: شجرة جانبية قد تحمل core.hooksPath مطلقًا في config.worktree يشير إلى المجلد الرئيسي،
فتعمل بخطّافات الرئيسي القديمة لا بخطّافات فرعها. نكتب القيمة النسبية على مستوى الشجرة فتغلب."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    subprocess.run(['git', 'config', 'core.hooksPath', '.githooks'], cwd=ROOT, check=True)
    # على مستوى الشجرة أيضًا إن فُعّلت extensions.worktreeConfig، ليغلب أي قيمة مطلقة موروثة.
    enabled = subprocess.run(['git', 'config', '--get', 'extensions.worktreeConfig'], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip() == 'true'
    if enabled:
        subprocess.run(['git', 'config', '--worktree', 'core.hooksPath', '.githooks'], cwd=ROOT, check=True)
    if os.name != 'nt':
        for hook in (ROOT / '.githooks').iterdir():
            hook.chmod(hook.stat().st_mode | 0o111)
    print('فُعّلت الحرّاس: pre-commit (التجميد)، commit-msg (سبب التجاوز)، pre-push (الفحص).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
