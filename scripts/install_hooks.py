"""تفعيل حرّاس Git مرة واحدة لكل نسخة من المستودع: core.hooksPath = .githooks"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    subprocess.run(['git', 'config', 'core.hooksPath', '.githooks'], cwd=ROOT, check=True)
    if os.name != 'nt':
        for hook in (ROOT / '.githooks').iterdir():
            hook.chmod(hook.stat().st_mode | 0o111)
    print('فُعّلت الحرّاس: pre-commit (التجميد)، commit-msg (سبب التجاوز)، pre-push (الفحص).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
