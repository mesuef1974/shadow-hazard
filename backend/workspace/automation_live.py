"""Live, read-only status for the automation map. Plain code, zero model tokens.

Sources: the guards' event log (.local/automation-events.jsonl), Git, the docs, GitHub Actions via
the owner's `gh` CLI (cached), and the platform's own automation records. Each source fails on
its own and reports "unavailable" instead of breaking the whole snapshot.
"""
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.utils import timezone

REPO = Path(settings.BASE_DIR).parent
EVENTS = REPO / '.local' / 'automation-events.jsonl'
TAIL_BYTES = 65_536
GITHUB_TTL = 60
GIT_TTL = 5
# A console window must never flash on Windows while the page polls git or gh.
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
HEARTBEAT_FRESH = {'local': timedelta(seconds=20), 'session_bridge': timedelta(minutes=6)}
_cache = {}


def _cached(key, ttl, build):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = build()
    _cache[key] = (time.monotonic(), value)
    return value


def reset_cache():
    _cache.clear()


def _run(command, timeout=5):
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=timeout, creationflags=NO_WINDOW)
    if result.returncode:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(message[-1][:120] if message else f'exit {result.returncode}')
    return result.stdout


def _commit(ref):
    try:
        values = _run(['git', 'log', '-1', '--format=%h%x00%s%x00%cI', ref]).strip().split('\0')
    except RuntimeError:
        return None
    return dict(zip(('sha', 'subject', 'at'), values, strict=False))


def events(path=None, recent=8):
    path = Path(path or EVENTS)
    if not path.exists():
        return {'available': True, 'latest': {}, 'recent': []}
    with path.open('rb') as handle:
        handle.seek(max(0, path.stat().st_size - TAIL_BYTES))
        lines = handle.read().decode('utf-8', 'replace').splitlines()
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and {'at', 'kind', 'result'} <= row.keys():
            rows.append({key: str(row.get(key, ''))[:200] for key in ('at', 'kind', 'result', 'detail')})
    latest = {}
    for row in rows:
        latest[row['kind']] = row
        if row['result'] == 'override':
            latest['override'] = row
    return {'available': True, 'latest': latest, 'recent': rows[-recent:][::-1]}


def git_state():
    def build():
        dirty = _run(['git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no'])
        hooks = subprocess.run(['git', 'config', '--get', 'core.hooksPath'], cwd=REPO, capture_output=True,
                               text=True, timeout=5, creationflags=NO_WINDOW).stdout.strip()
        return {'available': True, 'branch': _run(['git', 'branch', '--show-current']).strip(),
                'head': _commit('HEAD'), 'main': _commit('origin/main'),
                'uncommitted': len([line for line in dirty.splitlines() if line.strip()]),
                'hooks_active': hooks.replace('\\', '/').rstrip('/') == '.githooks'}
    return _cached('git', GIT_TTL, build)


def docs_state():
    scripts = str(REPO / 'scripts')
    if scripts not in sys.path:
        sys.path.append(scripts)
    import session_start  # the same reader the SessionStart hook uses

    handoff = session_start.handoff(REPO)
    tasks = session_start.open_tasks(REPO)
    return {'available': True, 'open_tasks': len(tasks), 'tasks': tasks[:6],
            'handoff': handoff.name if handoff else '',
            'handoff_at': datetime.fromtimestamp(handoff.stat().st_mtime, tz=dt_timezone.utc).isoformat()
            if handoff else ''}


def github_state():
    def build():
        gh = shutil.which('gh')
        if not gh:
            return {'available': False, 'reason': 'أداة GitHub (gh) غير مثبتة على هذا الحاسوب'}
        fields = 'workflowName,headBranch,headSha,status,conclusion,event,createdAt,updatedAt,url'
        try:
            runs = json.loads(_run([gh, 'run', 'list', '--limit', '12', '--json', fields], 12) or '[]')
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
            return {'available': False, 'reason': ('تعذر سؤال GitHub: ' + str(error))[:160]}
        keep = ('workflowName', 'headBranch', 'status', 'conclusion', 'event', 'createdAt', 'updatedAt', 'url')
        return {'available': True,
                'runs': [{key: run.get(key) for key in keep} | {'sha': str(run.get('headSha', ''))[:7]}
                         for run in runs if run.get('workflowName') == 'CI'][:6]}
    return _cached('github', GITHUB_TTL, build)


def platform_state():
    from execution_context.models import SessionDispatch
    from operations.models import AutomationIssue, BackupRun
    from token_usage.models import SyncRun

    from .models import DispatcherHeartbeat, ModelJob

    now = timezone.now()
    beats = [{'key': row.key, 'state': row.state, 'at': row.updated_at.isoformat(),
              'online': row.state == 'running'
              and row.updated_at > now - HEARTBEAT_FRESH.get(row.key, timedelta(minutes=2)),
              'error': (row.last_error or '')[:140]}
             for row in DispatcherHeartbeat.objects.order_by('key')]
    sync = SyncRun.objects.order_by('-started_at').first()
    backup = BackupRun.objects.order_by('-started_at').first()
    return {'available': True, 'heartbeats': beats,
            'jobs': {state: ModelJob.objects.filter(state=state).count()
                     for state in ('queued', 'running', 'uncertain', 'needs_review', 'blocked')},
            'outbox': {state: SessionDispatch.objects.filter(state=state).count()
                       for state in ('queued', 'uncertain')},
            'sync': {'at': (sync.finished_at or sync.started_at).isoformat(), 'success': sync.success}
            if sync else None,
            'backup': {'at': (backup.finished_at or backup.started_at).isoformat(), 'state': backup.state}
            if backup else None,
            'open_issues': AutomationIssue.objects.filter(state='open').count()}


def _safe(build):
    try:
        return build()
    except Exception as error:  # one broken source must not hide the others
        return {'available': False, 'reason': f'{type(error).__name__}: {error}'[:160]}


def snapshot():
    return {'generated_at': timezone.now().isoformat(), 'zero_tokens': True,
            'events': _safe(events), 'git': _safe(git_state), 'docs': _safe(docs_state),
            'github': _safe(github_state), 'platform': _safe(platform_state)}
