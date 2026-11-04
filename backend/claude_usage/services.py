"""Token-free import and summaries over the normalized tables."""
import os
import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Min, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from .models import AiModel, ClaudeMessage, ClaudeSession, GitBranch, ImportFile, ImportRun, UsageSource
from .parser import FIELDS, parse_line

FAMILY = re.compile(r'(opus|sonnet|haiku|fable|mythos)', re.I)


def default_sources():
    """(kind, directory, only_this_repo): local Claude Code transcripts filtered to this repository,
    and session files exported into .local/claude-usage/ (they belong to it already)."""
    configured = getattr(settings, 'CLAUDE_USAGE_SOURCES', None)
    if configured is not None:
        return [(kind, Path(path), bool(only)) for kind, path, only in configured]
    config_dir = os.environ.get('CLAUDE_CONFIG_DIR')
    projects = Path(config_dir) / 'projects' if config_dir else Path.home() / '.claude' / 'projects'
    return [('claude_projects', projects, True),
            ('session_export', settings.BASE_DIR.parent / '.local' / 'claude-usage', False)]


def _norm(path):
    return str(path).replace('\\', '/').rstrip('/').casefold()


def in_repo(cwd, root=None):
    root, cwd = _norm(root or settings.BASE_DIR.parent), _norm(cwd)
    return bool(cwd) and (cwd == root or cwd.startswith(root + '/'))


def _project():
    from workspace.models import Project
    project_id = getattr(settings, 'CLAUDE_USAGE_PROJECT_ID', None)
    if project_id:
        return Project.objects.filter(pk=project_id).first()
    projects = list(Project.objects.all()[:2])
    return projects[0] if len(projects) == 1 else None


def _ai_model(name, cache):
    if name not in cache:
        match = FAMILY.search(name)
        cache[name], _ = AiModel.objects.get_or_create(
            name=name, defaults={'family': match.group(1).capitalize() if match else ''})
    return cache[name]


def _branch(name, cache):
    if not name:
        return None
    if name not in cache:
        cache[name], _ = GitBranch.objects.get_or_create(name=name[:200])
    return cache[name]


def _read_new_lines(path, record):
    stat = path.stat()
    if stat.st_size == record.size and stat.st_mtime == record.mtime:
        return None, stat
    start = record.offset if record.offset <= stat.st_size else 0
    with path.open('rb') as handle:
        handle.seek(start)
        data = handle.read()
    end = data.rfind(b'\n') + 1
    record.offset = start + end
    return data[:end].decode('utf-8', 'replace').splitlines(), stat


def _store(replies, record, run, caches):
    merged = {}
    for reply in replies:  # Streaming writes one line per content block; keep the largest counters.
        kept = merged.get(reply.message_id)
        merged[reply.message_id] = reply if kept is None else replace(
            kept, usage={name: max(kept.usage[name], reply.usage[name]) for name in FIELDS})
    if not merged:
        return
    project = caches.setdefault('project', _project())
    sessions = {}
    for reply in merged.values():
        if reply.session_id not in sessions:
            session, _ = ClaudeSession.objects.get_or_create(
                session_id=reply.session_id, defaults={'surface': reply.surface, 'cwd': reply.cwd[:500], 'project': project})
            sessions[reply.session_id] = session
    existing = {m.message_id: m for m in ClaudeMessage.objects.filter(message_id__in=list(merged))}
    new = []
    for message_id, reply in merged.items():
        current = existing.get(message_id)
        if current is None:
            new.append(ClaudeMessage(message_id=message_id, session=sessions[reply.session_id],
                                     model=_ai_model(reply.model, caches.setdefault('models', {})),
                                     branch=_branch(reply.git_branch, caches.setdefault('branches', {})),
                                     file=record, at=reply.at, is_subagent=reply.is_subagent, **reply.usage))
        elif any(reply.usage[name] > getattr(current, name) for name in FIELDS):
            for name in FIELDS:
                setattr(current, name, max(reply.usage[name], getattr(current, name)))
            current.save(update_fields=list(FIELDS))
            run.messages_updated += 1
    ClaudeMessage.objects.bulk_create(new)
    run.messages_new += len(new)


def import_usage(sources=None):
    """Read new lines only. A run is recorded only when it stored replies or failed, so the
    worker's 30-second cycle leaves no rows while other projects' transcripts keep growing."""
    run, caches = ImportRun(started_at=timezone.now()), {}
    try:
        for kind, directory, only_repo in (sources or default_sources()):
            directory = Path(directory)
            if not directory.is_dir():
                continue
            source, _ = UsageSource.objects.update_or_create(
                path=str(directory), defaults={'kind': kind, 'only_this_repo': only_repo})
            for path in sorted(directory.rglob('*.jsonl')):
                with transaction.atomic():
                    record, _ = ImportFile.objects.select_for_update().get_or_create(
                        source=source, relative_path=str(path.relative_to(directory)))
                    lines, stat = _read_new_lines(path, record)
                    if lines is None:
                        continue
                    run.files_read += 1
                    run.lines_read += len(lines)
                    replies = []
                    for line in lines:
                        reply = parse_line(line)
                        if reply is None:
                            continue
                        if not reply.session_id:
                            reply = replace(reply, session_id=path.stem)
                        if only_repo and not in_repo(reply.cwd):
                            run.skipped_lines += 1
                            continue
                        replies.append(reply)
                    record.size, record.mtime = stat.st_size, stat.st_mtime
                    record.save()
                    _store(replies, record, run, caches)
        run.ok = True
    except Exception as exc:  # Shown on the page; the dispatcher keeps running.
        run.error = type(exc).__name__
    run.finished_at = timezone.now()
    if run.messages_new or run.messages_updated or not run.ok:
        run.save()
    return run


def _with_fresh(row):
    row['fresh'] = row['input_tokens'] + row['cache_creation_input_tokens'] + row['output_tokens']
    return row


def _sums():
    return {name: Sum(name, default=0) for name in FIELDS}


def totals(queryset):
    return _with_fresh(queryset.aggregate(**_sums(), replies=Count('id'), first=Min('at'), last=Max('at')))


def by_model(queryset):
    rows = queryset.values('model__name', 'model__family').annotate(**_sums(), replies=Count('id'))
    return sorted((_with_fresh(dict(row)) for row in rows), key=lambda row: -row['fresh'])


def by_day(queryset):
    rows = (queryset.annotate(day=TruncDate('at', tzinfo=timezone.get_current_timezone()))
            .values('day').annotate(**_sums(), replies=Count('id')).order_by('-day'))
    return [_with_fresh(dict(row)) for row in rows]


def chart_data(now, messages, session):
    """Numbers for the page's charts: every day of the last 30 (empty days are zero), the current
    session hour by hour, and the 30-day split by model."""
    zone = timezone.get_current_timezone()
    today = timezone.localtime(now, zone).date()
    days = {row['day']: row for row in by_day(messages.filter(at__gt=now - timedelta(days=30), at__lte=now))}
    daily = []
    for offset in range(29, -1, -1):
        day = today - timedelta(days=offset)
        row = days.get(day, {})
        daily.append({'label': day.isoformat(), 'new_input': (row.get('input_tokens') or 0) + (row.get('cache_creation_input_tokens') or 0),
                      'output': row.get('output_tokens') or 0, 'cache_read': row.get('cache_read_input_tokens') or 0,
                      'replies': row.get('replies') or 0})
    hourly = []
    if session is not None:
        rows = (messages.filter(session=session).annotate(hour=TruncHour('at', tzinfo=zone)).values('hour')
                .annotate(**_sums(), replies=Count('id')).order_by('hour'))
        for row in rows:
            hourly.append({'label': timezone.localtime(row['hour'], zone).strftime('%m-%d %H:00'),
                           'new_input': row['input_tokens'] + row['cache_creation_input_tokens'],
                           'output': row['output_tokens'], 'cache_read': row['cache_read_input_tokens'], 'replies': row['replies']})
    models = [{'label': row['model__name'], 'fresh': row['fresh'], 'replies': row['replies']}
              for row in by_model(messages.filter(at__gt=now - timedelta(days=30), at__lte=now))]
    return {'daily': daily, 'hourly': hourly, 'models': models}


def sessions_overview(limit=20):
    return (ClaudeSession.objects.annotate(first=Min('messages__at'), last=Max('messages__at'), replies=Count('messages'))
            .exclude(last=None).order_by('-last')[:limit])


def summary(now=None, session_id=None):
    now = now or timezone.now()
    messages = ClaudeMessage.objects.all()
    overview = list(sessions_overview())
    session = (ClaudeSession.objects.filter(session_id=session_id).first() if session_id
               else (overview[0] if overview else None))
    windows = []
    for days in (7, 30):
        scoped = messages.filter(at__gt=now - timedelta(days=days), at__lte=now)
        windows.append({'days': days, 'totals': totals(scoped), 'models': by_model(scoped)})
    current = None
    if session is not None:
        scoped = messages.filter(session=session)
        current = {'session': session, 'totals': totals(scoped), 'models': by_model(scoped)}
    return {'now': now, 'session': current, 'windows': windows, 'sessions': overview,
            'charts': chart_data(now, messages, session),
            'daily': by_day(messages.filter(at__gt=now - timedelta(days=30), at__lte=now))}
