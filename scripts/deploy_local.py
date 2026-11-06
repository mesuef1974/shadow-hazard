"""النشر المحلي (ADR 0005): يسحب main ويهاجر ويعيد تشغيل المنصة، ويتراجع إن فشل شيء.

تشغّله مهمة ويندوز كل 5 دقائق، فلا سر في المستودع العام ولا منفذ مفتوح. لا يلمس نسخة متّسخة.
التراجع يعيد الشيفرة والتبعيات: الهجرة المطبّقة تبقى، فالهجرات تُكتب متوافقة مع الإصدار السابق.
الاستعمال: python scripts/deploy_local.py [--target <مسار النسخة>]
"""
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from automation_events import record
from deploy_alert import notify
from pr_refresh import refresh
from pr_updates import merge_small_update

PORT = 8011
HEALTH_TRIES = 20
IDLE_TRIES = 3
WORKER_VERSION = Path('backend') / '.local' / 'worker-version'
WORKER_GRACE = 600
ROOT = Path(__file__).resolve().parents[1]
FAILED = Path('.local') / 'deploy-failed'
PACKAGES = Path('.local') / 'deploy-packages.txt'


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


def freeze(root):
    """ما في بيئة بايثون الآن بنسخه الدقيقة."""
    out = subprocess.run([python_of(root), '-m', 'pip', 'freeze'], cwd=root, capture_output=True,
                         text=True, encoding='utf-8', errors='replace')
    if out.returncode != 0:
        raise RuntimeError(f'pip freeze: {out.stderr.strip()[:200]}')
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def package_name(line):
    return re.split(r'[=@ ]', line, maxsplit=1)[0].lower()


def restore_packages(root, saved, run=run_step):
    """يعيد البيئة إلى ما كانت عليه قبل النشر بالضبط، فما أضافه النشر يُحذف وما رقّاه يعود.

    لا يُقرأ ملف المتطلبات هنا: البيئة المحفوظة هي ما كان يعمل فعلًا.
    """
    try:
        now = freeze(root)
        if now == saved:
            return True
        python = python_of(root)
        extra = sorted({package_name(line) for line in now}
                       - {package_name(line) for line in saved})
        if extra and not run(root, [python, '-m', 'pip', 'uninstall', '-q', '-y', *extra]):
            return False
        path = root / PACKAGES
        path.parent.mkdir(exist_ok=True)
        path.write_text('\n'.join(sorted(saved)) + '\n', encoding='utf-8')
        # --no-deps: القائمة كاملة بنسخها، فلا حاجة لحلّ تبعيات في منتصف التراجع.
        return (run(root, [python, '-m', 'pip', 'install', '-q', '--no-deps', '-r', str(path)])
                and freeze(root) == saved)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


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


def healthy(tries=HEALTH_TRIES):
    for _ in range(tries):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{PORT}/', timeout=3)
            return True
        except urllib.error.HTTPError as error:
            return error.code < 500  # صفحة الدخول أو 404 تعني أن الخادم حيّ.
        except OSError:
            time.sleep(1)
    return False


def restart(root):
    stop_platform()
    start_platform(root)
    return healthy()


def worker_problem(root, current, now=time.time):
    """العامل يتجدد وحده حين تتغير الشيفرة (platform_worker)؛ هنا يُقرأ ما أبلغ به فقط."""
    try:
        age = now() - (root / FAILED).stat().st_mtime
    except OSError:
        return ''
    path = root / WORKER_VERSION
    running = path.read_text(encoding='utf-8').strip() if path.exists() else ''
    if running == current or age < WORKER_GRACE:
        return ''
    return f'العامل لم يتجدد إلى {current[:7]}'


def rollback(root, previous, saved, checkout, restart_fn):
    """الشيفرة ثم التبعيات ثم المنصة: المنصة متوقفة كي لا تمسك ملفات المكتبات أثناء إعادتها."""
    checkout(previous)
    stop_platform()
    note = f'تراجعت إلى {previous[:7]}'
    if not restore_packages(root, saved):
        note += '، ولم تعد التبعيات'
    if not restart_fn(root):
        note += '، ولم تعد المنصة'
    return note


def platform_problem():
    """مهمة المنصة تعيدها خلال 5 دقائق، فهنا يُسجَّل التوقف ويُنبَّه عنه ولا يُعاد تشغيلها."""
    return '' if healthy(IDLE_TRIES) else f'المنصة لا تجيب على {PORT}'


def watch(root, current, target):
    """الدورة الخاملة. الزوال يُسجَّل معلومة لا نجاحًا: نجاح deploy يعني نشرًا في سجل الأتمتة."""
    problem = platform_problem()
    if not problem and current == target:
        problem = worker_problem(root, current)
    if not problem and current != target:
        return ''  # إيداع فشل نشره يبقى عطلًا قائمًا حتى يأتي أحدث منه.
    if notify(root, problem):
        record('deploy', 'fail' if problem else 'info', problem or 'زال العطل', root)
    return problem


def deploy(root, target, checkout, run, restart_fn, previous):
    """يرجع (نجح؟، السبب). مفصول عن النظام ليُختبر مسار التراجع."""
    saved = freeze(root)
    checkout(target)
    for name, command in steps(root):
        if not run(root, command):
            return False, f'فشل {name}؛ {rollback(root, previous, saved, checkout, restart_fn)}'
    if restart_fn(root):
        return True, f'نُشر {target[:7]}'
    return False, f'المنصة لم تستجب؛ {rollback(root, previous, saved, checkout, restart_fn)}'


def run(root):
    refresh(root)
    git(root, 'fetch', '-q', 'origin', 'main')
    current, target = git(root, 'rev-parse', 'HEAD'), git(root, 'rev-parse', 'origin/main')
    # غير المتعقَّب لا يمنع النشر: ملفات المالك المحلية تبقى ولا يمسها checkout.
    marker = root / FAILED
    failed = marker.read_text(encoding='utf-8').strip() if marker.exists() else ''
    action = plan(current, target, bool(git(root, 'status', '--porcelain', '--untracked-files=no')),
                  failed)
    if action == 'idle':
        # لا ترقية فوق عطل قائم أو نشر فاشل، فلا يختلط سببان.
        if not watch(root, current, target) and current == target:
            merge_small_update(root)
        return 0
    if action == 'refuse':
        print('النسخة متّسخة؛ لم أنشر.')
        record('deploy', 'fail', 'نسخة متّسخة', root)
        notify(root, 'نسخة متّسخة')
        return 1
    ok, reason = deploy(root, target, lambda sha: git(root, 'checkout', '-q', '--detach', sha),
                        run_step, restart, current)
    print(reason)
    marker.parent.mkdir(exist_ok=True)
    marker.write_text('' if ok else target, encoding='utf-8')
    record('deploy', 'pass' if ok else 'fail', reason, root)
    notify(root, '' if ok else reason)
    return 0 if ok else 1


def main(argv):
    if sys.stdout:  # pythonw في مهمة ويندوز بلا stdout.
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    root = Path(argv[argv.index('--target') + 1]).resolve() if '--target' in argv else ROOT
    try:
        return run(root)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        reason = f'تعطل النشر: {error}'
        record('deploy', 'fail', reason, root)
        notify(root, reason)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
