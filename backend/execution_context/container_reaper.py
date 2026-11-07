"""تنظيف حاويات القراءة ومجلداتها الخاملة: كود فقط، بلا نموذج ولا توكنز.

كل حاوية أو مجلد يفتحه المشروع يحمل وسمين: المالك ووقت الإنشاء. فإن سقطت العملية قبل
`close()` وفات `atexit` (قتل مفاجئ أو انقطاع كهرباء) وجد المراقب البقايا بوسمها وأزالها
بعد انقضاء المهلة. لا يمس إلا ما يحمل وسم المشروع، فحاويات غيره لا تتأثر أبدًا.
"""
import subprocess
import time

OWNER_LABEL = 'shadow-hazard.owner'
OWNER = 'readonly-runtime'
CREATED_LABEL = 'shadow-hazard.created'
TTL_SECONDS = 6 * 3600
INTERVAL_SECONDS = 300
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_last = {'at': None}


def labels(now=None):
    """وسما المالك ووقت الإنشاء، يُضافان إلى كل `docker run` و`docker volume create`."""
    created = int(time.time() if now is None else now)
    return ['--label', f'{OWNER_LABEL}={OWNER}', '--label', f'{CREATED_LABEL}={created}']


def docker(run, *args):
    return run(['docker', *args], capture_output=True, text=True, encoding='utf-8',
               timeout=30, creationflags=NO_WINDOW)


def rows(run, kind):
    """أزواج (الاسم، وقت الإنشاء) لما يحمل وسم المشروع. docker غائب أو معطل يعني لا شيء."""
    listing = ('ps', '-a') if kind == 'container' else ('volume', 'ls')
    name = '{{.Names}}' if kind == 'container' else '{{.Name}}'
    try:
        result = docker(run, *listing, '--filter', f'label={OWNER_LABEL}={OWNER}',
                        '--format', name + '\t{{.Label "' + CREATED_LABEL + '"}}')
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode:
        return []
    found = []
    for line in result.stdout.splitlines():
        name_, _, created = line.partition('\t')
        if name_.strip() and created.strip().isdigit():
            found.append((name_.strip(), int(created.strip())))
    return found  # وقت غير مفهوم يعني: لا نعرف عمره فلا نمسه.


def reap(run=subprocess.run, now=time.time, ttl=TTL_SECONDS):
    """يزيل الحاويات ثم المجلدات التي تجاوز عمرها المهلة. يرجع ما أزاله."""
    limit = now() - ttl
    removed = {'containers': [], 'volumes': []}
    for name, created in rows(run, 'container'):
        if created <= limit and _remove(run, 'rm', '--force', name):
            removed['containers'].append(name)
    for name, created in rows(run, 'volume'):
        if created <= limit and _remove(run, 'volume', 'rm', name):
            removed['volumes'].append(name)
    return removed


def _remove(run, *args):
    try:
        return docker(run, *args).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def maintenance(clock=time.monotonic, **kwargs):
    """خطوة صيانة بلا توكنز يستدعيها العامل الوحيد كل 30 ث؛ تعمل فعليًا كل 5 دقائق."""
    if _last['at'] is not None and clock() - _last['at'] < INTERVAL_SECONDS:
        return {'errors': []}
    _last['at'] = clock()
    try:
        reap(**kwargs)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {'errors': [{'operation': 'container_reaper',
                            'error_type': type(error).__name__, 'error': str(error)[:100]}]}
    return {'errors': []}
