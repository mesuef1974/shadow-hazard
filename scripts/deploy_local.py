"""النشر المحلي (ADR 0005): يسحب main ويهاجر ويعيد تشغيل المنصة، ويتراجع إن فشل شيء.

تشغّله مهمة ويندوز كل 5 دقائق، فلا سر في المستودع العام ولا منفذ مفتوح. لا يلمس نسخة متّسخة.
التراجع يعيد الشيفرة فقط: الهجرة المطبّقة تبقى، فالهجرات تُكتب متوافقة مع الإصدار السابق.
الاستعمال: python scripts/deploy_local.py [--target <مسار النسخة>]
"""
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from automation_events import record
from pr_refresh import refresh

PORT = 8011
HEALTH_TRIES = 20
WORKER_TASK = 'ShadowDanger-ModelDispatcher'
ROOT = Path(__file__).resolve().parents[1]
FAILED = Path('.local') / 'deploy-failed'


def plan(current, target, dirty, failed=''):
    """إيداع فشل نشره لا يُعاد: وإلا تكررت النسخة الاحتياطية وإيقاف المنصة كل 5 دقائق."""
    if dirty:
        return 'refuse'
    return 'idle' if target in (current, failed) else 'deploy'


def git(root, *args):
    # core.bare=true في الإعداد المشترك يعطّل أوامر الشجرة في المجلد الرئيسي، و-c لا يغلبه؛
    # --work-tree وحده يغلبه.
    out = subprocess.run(['git', f'--work-tree={root}', *args], cwd=root, capture_output=True,
                         text=True, encoding='utf-8', errors='replace')
    if out.returncode != 0:
        raise RuntimeError(f'git {args[0]}: {out.stderr.strip()[:200]}')
    return out.stdout.strip()


def python_of(root):
    for name in ('.venv/Scripts/python.exe', '.venv/bin/python'):
        if (root / name).exists():
            return str(root / name)
    raise RuntimeError('لا .venv في نسخة النشر')


def steps(root):
    python = python_of(root)
    manage = str(root / 'backend' / 'manage.py')
    return [
        ('التبعيات', [python, '-m', 'pip', 'install', '-q', '-r',
                      str(root / 'backend' / 'requirements.txt')]),
        ('فحص Django', [python, manage, 'check']),
        ('النسخة الاحتياطية', [python, manage, 'create_project_backup', '--before-migration']),
        ('الهجرة', [python, manage, 'migrate', '--noinput']),
        ('بيانات المسابقة', [python, manage, 'import_competition_data']),
    ]


def run_step(root, command):
    return subprocess.run(command, cwd=root, capture_output=True, text=True, encoding='utf-8',
                          errors='replace').returncode == 0


def stop_platform():
    """يوقف ما يستمع على منفذ المنصة؛ لا شيء يعني أنها متوقفة أصلًا."""
    script = (f'Get-NetTCPConnection -LocalPort {PORT} -ErrorAction SilentlyContinue | '
              'Select-Object -Unique -ExpandProperty OwningProcess | '
              'ForEach-Object { Stop-Process -Id $_ -Force }')
    subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True)


def start_platform(root):
    flags = (getattr(subprocess, 'DETACHED_PROCESS', 0)
             | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    subprocess.Popen([python_of(root), str(root / 'tools' / 'run-platform.py')], cwd=root,
                     creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def healthy():
    for _ in range(HEALTH_TRIES):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{PORT}/', timeout=3)
            return True
        except urllib.error.HTTPError as error:
            return error.code < 500  # صفحة الدخول أو 404 تعني أن الخادم حيّ.
        except OSError:
            time.sleep(1)
    return False


def restart_worker():
    """العامل يحمّل الشيفرة مرة، فيعاد مع كل نشر. مهمته تحرسه؛ لا يوقف فشلها النشر."""
    # التشغيل قبل انتهاء الإيقاف يُهمَل بصمت فيبقى العامل متوقفًا، فننتظر انتهاءه.
    script = (f"Stop-ScheduledTask -TaskName {WORKER_TASK} -ErrorAction SilentlyContinue; "
              f"$n=0; while ((Get-ScheduledTask -TaskName {WORKER_TASK}).State -eq 'Running' "
              "-and $n -lt 20) { Start-Sleep 1; $n++ }; "
              f"Start-ScheduledTask -TaskName {WORKER_TASK} -ErrorAction SilentlyContinue")
    subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True)


def restart(root):
    stop_platform()
    start_platform(root)
    restart_worker()
    return healthy()


def deploy(root, target, checkout, run, restart_fn, previous):
    """يرجع (نجح؟، السبب). مفصول عن النظام ليُختبر مسار التراجع."""
    checkout(target)
    for name, command in steps(root):
        if not run(root, command):
            checkout(previous)
            restart_fn(root)
            return False, f'فشل {name}؛ تراجعت إلى {previous[:7]}'
    if restart_fn(root):
        return True, f'نُشر {target[:7]}'
    checkout(previous)
    restart_fn(root)
    return False, f'المنصة لم تستجب؛ تراجعت إلى {previous[:7]}'


def main(argv):
    if sys.stdout:  # pythonw في مهمة ويندوز بلا stdout.
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    root = Path(argv[argv.index('--target') + 1]).resolve() if '--target' in argv else ROOT
    refresh(root)
    git(root, 'fetch', '-q', 'origin', 'main')
    current, target = git(root, 'rev-parse', 'HEAD'), git(root, 'rev-parse', 'origin/main')
    # غير المتعقَّب لا يمنع النشر: ملفات المالك المحلية تبقى ولا يمسها checkout.
    marker = root / FAILED
    failed = marker.read_text(encoding='utf-8').strip() if marker.exists() else ''
    action = plan(current, target, bool(git(root, 'status', '--porcelain', '--untracked-files=no')),
                  failed)
    if action == 'idle':
        return 0
    if action == 'refuse':
        print('النسخة متّسخة؛ لم أنشر.')
        record('deploy', 'fail', 'نسخة متّسخة', root)
        return 1
    ok, reason = deploy(root, target, lambda sha: git(root, 'checkout', '-q', '--detach', sha),
                        run_step, restart, current)
    print(reason)
    marker.parent.mkdir(exist_ok=True)
    marker.write_text('' if ok else target, encoding='utf-8')
    record('deploy', 'pass' if ok else 'fail', reason, root)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
