"""Bounded read-only import of diagnostic context estimates; never billing/actions."""
import hashlib
import json
import re
import sqlite3
import uuid
import time
from datetime import datetime, timedelta, timezone as utc
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from workspace.models import SessionCharter
from .models import ContextObservation, ContextSource, TrackedSession

PARSER = 'codex-0.166.0-alpha.5.1-post-sampling-v1'
TARGET = 'codex_core::session::turn'
MAX_AGE = 120
SCHEMA = [('id','INTEGER'),('ts','INTEGER'),('ts_nanos','INTEGER'),('level','TEXT'),
    ('target','TEXT'),('feedback_log_body','TEXT'),('module_path','TEXT'),('file','TEXT'),
    ('line','INTEGER'),('thread_id','TEXT'),('process_uuid','TEXT'),('estimated_bytes','INTEGER')]
MESSAGE = 'post sampling token usage'
MARKER = ': post sampling token usage '
FIELDS = {'turn_id','total_usage_tokens','auto_compact_scope_tokens','auto_compact_scope_limit',
    'auto_compact_limit_scope','auto_compact_window_prefill_tokens','full_context_window_limit',
    'full_context_window_limit_reached','token_limit_reached','model_needs_follow_up','has_pending_input','needs_follow_up'}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def field(body, name):
    values = re.findall(r'\b'+name+r'\s*=\s*([^\s,}]+)', body)
    return values[-1].strip('"') if values else None


def parse(row, now):
    body = row['feedback_log_body']
    if not isinstance(body, str) or len(body) > 65536 or MARKER not in body:
        raise ValueError('invalid_message')
    if row['target'] != TARGET or row['module_path'] != TARGET or row['line'] != 645 or row['level'] != 'TRACE':
        raise ValueError('unapproved_provenance')
    prefix, suffix = body.rsplit(MARKER, 1)
    pairs = re.findall(r'(\w+)=([^ ]+)', suffix)
    values = dict(pairs)
    if len(values) != len(pairs) or set(values) != FIELDS:
        raise ValueError('unapproved_fields')
    thread = str(uuid.UUID(row['thread_id']))
    turn = str(uuid.UUID(values['turn_id']))
    threads = re.findall(r'\bthread_id\s*=\s*([0-9a-f-]{36})', prefix)
    if not threads or any(t != thread for t in threads):
        raise ValueError('conflicting_thread')
    turns = re.findall(r'\bturn\.id=([0-9a-f-]{36})', prefix)
    if not turns or any(t != turn for t in turns):
        raise ValueError('conflicting_turn')
    process = row['process_uuid']
    if not isinstance(process, str) or not re.fullmatch(r'pid:\d+:[0-9a-f-]{36}', process):
        raise ValueError('invalid_process')
    def number(name, optional=False):
        value = values[name]
        if optional and value == 'None':
            return None
        pattern = r'Some\((\d+)\)' if optional else r'\d+'
        if not value or not re.fullmatch(pattern, value):
            raise ValueError('invalid_number')
        return int(value[5:-1] if optional else value)
    if values['auto_compact_limit_scope'] != 'Total' or number('auto_compact_window_prefill_tokens',True) is not None:
        raise ValueError('unapproved_scope')
    current = number('total_usage_tokens')
    limit = number('full_context_window_limit', True)
    if not limit or current > 10**9 or limit > 10**9:
        raise ValueError('invalid_capacity')
    if number('auto_compact_scope_tokens') != current:
        raise ValueError('conflicting_scope')
    number('auto_compact_scope_limit',True)
    for key in ['full_context_window_limit_reached','token_limit_reached','model_needs_follow_up','has_pending_input','needs_follow_up']:
        if values[key] not in {'true','false'}:
            raise ValueError('invalid_flag')
    if (values['full_context_window_limit_reached'] == 'true') != (current >= limit):
        raise ValueError('conflicting_full_window')
    if type(row['ts']) is not int or type(row['ts_nanos']) is not int or not 0 <= row['ts_nanos'] < 10**9:
        raise ValueError('invalid_timestamp')
    stamp = datetime.fromtimestamp(row['ts'], utc.utc) + timedelta(microseconds=row['ts_nanos']//1000)
    if stamp > now + timedelta(seconds=5) or row['id'] <= 0:
        raise ValueError('future_timestamp')
    models = set(re.findall(r'\bmodel\s*=\s*([a-zA-Z0-9_.-]+)', prefix))
    model = next(iter(models)) if len(models) == 1 else ''
    return dict(thread_id=thread, turn_id=turn, process_uuid=process, sampled_at=stamp,
        current=current, limit=limit, model=model, source_row=row['id'],
        source_digest=digest(body), parser_version=PARSER)


class SchemaChanged(ValueError):
    pass


def synchronize_context():
    """Called under token-usage-sync lock. Source failure does not stop usage sync."""
    now = timezone.now()
    path = Path(getattr(settings, 'CONTEXT_USAGE_SOURCE', Path.home()/'.codex'/'logs_2.sqlite'))
    try:
        return _import(path, now)
    except Exception as exc:
        code = 'schema_changed' if isinstance(exc, SchemaChanged) else 'unavailable'
        # Never persist exception messages or raw source bodies.
        try:
            ContextSource.objects.update_or_create(key='codex-runtime', defaults={
                'status':code,'checked_at':now}, create_defaults={
                'generation':uuid.uuid4(),'identity':'','status':code,'checked_at':now})
        except Exception:
            # An unapplied migration or a locked destination must not kill the watcher.
            pass
        return {'added':0, 'status':code}


def _import(path, now):
    stat = path.stat()
    identity = digest(str(path.resolve())+':'+str(stat.st_dev)+':'+str(stat.st_ino)+':'+str(getattr(stat, 'st_birthtime', '')))
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic()+2
    conn.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        schema = [(r['name'],r['type']) for r in conn.execute('PRAGMA table_info(logs)')]
        if schema != SCHEMA:
            raise SchemaChanged()
        highwater = conn.execute('SELECT COALESCE(MAX(id),0) FROM logs').fetchone()[0]
        with transaction.atomic():
            state, _ = ContextSource.objects.select_for_update().get_or_create(key='codex-runtime', defaults={
                'generation':uuid.uuid4(),'identity':identity,'checked_at':now})
            anchor = conn.execute('SELECT id,ts,ts_nanos,process_uuid,target FROM logs WHERE id=?',(state.highwater,)).fetchone()
            replaced = (state.identity != identity or highwater < state.highwater or
                (anchor is not None and state.anchor_digest and digest(json.dumps(list(anchor))) != state.anchor_digest))
            if replaced:
                state.generation = uuid.uuid4()
                state.cursors = {}
            added = rejected = 0
            charters = SessionCharter.objects.exclude(thread_id='').order_by('pk')
            for charter in charters:
                if SessionCharter.objects.filter(thread_id=charter.thread_id).exclude(pk=charter.pk).exists():
                    continue
                binding = str(charter.pk)+':'+charter.thread_id
                cursor = state.cursors.get(binding)
                rows = conn.execute('SELECT id,ts,ts_nanos,level,target,module_path,line,thread_id,process_uuid,feedback_log_body FROM logs '
                    'WHERE thread_id=? AND target=? AND id>? AND feedback_log_body LIKE ? ORDER BY id '+('ASC' if cursor else 'DESC')+' LIMIT 500',
                    (charter.thread_id,TARGET,cursor or 0,'%'+MESSAGE+'%')).fetchall()
                for row in reversed(rows) if cursor is None else rows:
                    try:
                        values = parse(row, now)
                    except (ValueError,TypeError,OverflowError,OSError):
                        rejected += 1
                        continue
                    key = digest(':'.join([str(state.generation),values['process_uuid'],str(row['id']),values['thread_id'],values['turn_id']]))
                    if not SessionCharter.objects.filter(pk=charter.pk,thread_id=values['thread_id']).exists():
                        continue
                    _, created = ContextObservation.objects.get_or_create(key=key, defaults={
                        'charter':charter,'generation':state.generation,**values})
                    added += created
                if rows:
                    state.cursors[binding] = max(r['id'] for r in rows)
            latest_anchor = conn.execute('SELECT id,ts,ts_nanos,process_uuid,target FROM logs WHERE id=?',(highwater,)).fetchone()
            state.identity = identity
            state.schema_digest = digest(json.dumps(schema))
            state.highwater = highwater
            state.anchor_digest = digest(json.dumps(list(latest_anchor))) if latest_anchor else ''
            state.status = 'invalid_data' if rejected else ('invalid_data' if state.status == 'invalid_data' and not added else 'available')
            state.checked_at = now
            state.save()
        return {'added':added,'status':state.status}
    finally:
        conn.close()


def snapshot(project, session_id=None, period='week', now=None):
    now = now or timezone.now()
    state = ContextSource.objects.filter(key='codex-runtime').first()
    healthy = bool(state and state.status == 'available' and 0 <= (now-state.checked_at).total_seconds() <= MAX_AGE)
    charters = SessionCharter.objects.filter(project=project).exclude(thread_id='').order_by('pk')
    if session_id:
        tracked = TrackedSession.objects.filter(pk=session_id,project=project).first()
        charters = charters.filter(thread_id=tracked.external_id) if tracked else charters.none()
    result = []
    for charter in charters:
        rows = ContextObservation.objects.filter(charter=charter,thread_id=charter.thread_id)
        current_rows = rows.filter(generation=state.generation) if state else rows.none()
        latest = current_rows.order_by('-sampled_at','-pk').first()
        age = max(0,int((now-latest.sampled_at).total_seconds())) if latest else None
        conflict = SessionCharter.objects.filter(thread_id=charter.thread_id).exclude(pk=charter.pk).exists()
        fresh = bool(healthy and not conflict and latest and 0 <= (now-latest.sampled_at).total_seconds() <= MAX_AGE)
        history = rows
        if period in {'day','week'}:
            history = history.filter(sampled_at__gte=now-timedelta(days=1 if period=='day' else 7))
        history = list(history.order_by('-sampled_at','-pk')[:500])[::-1]
        points = [{'at':r.sampled_at.isoformat(),'used':r.current,'limit':r.limit,'percent':round(r.current/r.limit*100,2),
            'model':r.model,'process':r.process_uuid,'generation':str(r.generation),'turn':str(r.turn_id)} for r in history]
        result.append({'id':charter.pk,'name':charter.name,'fresh':fresh,
            'status':'حديث' if fresh else 'قديم' if healthy and latest and not conflict else 'غير متاح',
            'latest':latest,'age':age,'percent':round(latest.current/latest.limit*100,1) if fresh else None,
            'bar':min(100,latest.current/latest.limit*100) if fresh else 0,'points':points})
    return {'sessions':result,'source_status':state.status if state else 'unavailable',
        'checked_at':state.checked_at if state else None,'max_age':MAX_AGE,'parser':PARSER,
        'series':[{'id':r['id'],'name':r['name'],'points':r['points']} for r in result]}
