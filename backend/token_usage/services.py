import sqlite3
import hashlib
import json
import uuid
from pathlib import Path
from django.conf import settings
from django.db import transaction, DatabaseError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import (TrackedSession, TokenSample, SyncRun, CollectorState,
    UsageSourceState, LEGACY_SOURCE_GENERATION)
from .delegated import job_index, effective_samples, dashboard_samples


def synchronize():
    from .locking import local_lock
    with local_lock('token-usage-sync.lock') as acquired:
        if not acquired:
            return None
        from .runtime_context import synchronize_context
        synchronize_context()
        return _synchronize()


def _synchronize():
    run = SyncRun.objects.create()
    added = 0
    try:
        path = Path(settings.TOKEN_USAGE_SOURCE).resolve()
        stat = path.stat()
        identity = hashlib.sha256((str(path)+f':{stat.st_dev}:{stat.st_ino}:'+
            str(getattr(stat,'st_birthtime_ns',''))).encode()).hexdigest()
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            # Read one consistent source snapshot without locking the project DB.
            connection.execute('BEGIN')
            collector_payload = None
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'usage_collector_runs' in tables:
                collector=connection.execute('SELECT status,started_at,completed_at,heartbeat_at,source_files_updated,samples_inserted FROM usage_collector_runs ORDER BY run_id DESC LIMIT 1').fetchone()
                last_success=connection.execute("SELECT completed_at FROM usage_collector_runs WHERE status='success' ORDER BY run_id DESC LIMIT 1").fetchone()
                weekly=connection.execute('SELECT sampled_at,recorded_at,weekly_used_percent,weekly_remaining_percent,weekly_window_minutes,weekly_resets_at FROM usage_samples WHERE weekly_used_percent IS NOT NULL AND weekly_window_minutes=10080 ORDER BY sampled_at DESC,sample_id DESC LIMIT 1').fetchone()
                collector_data=dict(collector) if collector else {}
                collector_data['last_success_at']=last_success['completed_at'] if last_success else None
                collector_payload={'collector':collector_data,'weekly':dict(weekly) if weekly else {}}
            source_state, new_samples, metadata_updates, invalid_rows, source_changed = _prepare_samples(connection, identity)
        finally:
            connection.close()
        # synchronize() owns the interprocess sync lock throughout preparation
        # and commit. Only changed rows need the shared database's write lock.
        with transaction.atomic():
            if collector_payload is not None:
                CollectorState.objects.update_or_create(key='local',defaults={'payload':collector_payload})
            TokenSample.objects.bulk_create(new_samples, batch_size=500)
            TokenSample.objects.bulk_update(metadata_updates, ['effort','tier'], batch_size=500)
            source_state.save()
        added=len(new_samples)
        run.added=added
        run.success=not invalid_rows
        if invalid_rows:
            first_session,first_sample=invalid_rows[0]
            run.error_code='invalid_source_data'
            run.message=(f'اكتملت مزامنة جزئية؛ تعذر اعتماد {len(invalid_rows)} عينة. '
                f'أول موضع: الجلسة #{first_session}، العينة {first_sample}.')
        else:
            run.message='اكتملت مزامنة الجلسات المحددة للمشروع.'
            if source_changed:
                run.message+=' اكتُشف تغير في المصدر؛ حُفظ جيل جديد مع إبقاء البيانات السابقة.'
    except (sqlite3.Error, OSError, ValueError, KeyError, IndexError, TypeError, DatabaseError) as exc:
        added=0
        run.message='تعذرت قراءة مصدر الاستهلاك المحلي؛ آخر بيانات محفوظة ما زالت متاحة.'
        run.error_code = (
            'invalid_source_data' if isinstance(exc, (ValueError, KeyError, IndexError, TypeError)) else
            'source_unavailable' if isinstance(exc, (sqlite3.Error, OSError)) else
            'database_error'
        )
    finally:
        run.added=added;run.finished_at=timezone.now();run.save()
    return run


def _prepare_samples(connection, identity):
    highwater = connection.execute('SELECT COALESCE(MAX(sample_id),0) FROM usage_samples').fetchone()[0]
    anchor = _source_anchor(connection, highwater)
    source_state = UsageSourceState.objects.filter(key='local').first()
    source_changed = False
    if source_state is None:
        source_state = UsageSourceState(key='local',generation=LEGACY_SOURCE_GENERATION)
    else:
        source_changed = (source_state.identity != identity or highwater < source_state.highwater or
            (source_state.highwater > 0 and anchor_at_id(connection,source_state.highwater) != source_state.anchor_digest))
    generation = uuid.uuid4() if source_changed else source_state.generation
    prepared=[]
    invalid_rows=[]
    content_changed=False
    for session in TrackedSession.objects.filter(enabled=True).order_by('pk'):
        known={row.source_id:row for row in session.samples.filter(source_generation=generation).only(
            'session_id','source_id','sampled_at','model','total','input','cached','output','reasoning','context','context_limit','effort','tier')}
        seen=set(); valid=[]
        rows=connection.execute('SELECT * FROM usage_samples WHERE session_id=? ORDER BY sampled_at,sample_id',(session.external_id,))
        for row in rows:
            try:
                values=_source_sample(row)
            except (ValueError,KeyError,IndexError,TypeError,OverflowError):
                invalid_rows.append((session.pk,row['sample_id'] if 'sample_id' in row.keys() else None))
                continue
            source_id=values.pop('source_id')
            if source_id in seen:
                invalid_rows.append((session.pk,source_id))
                continue
            seen.add(source_id)
            old=known.get(source_id)
            if old and any(getattr(old,key)!=values[key] for key in ('sampled_at','model','total','input','cached','output','reasoning','context','context_limit')):
                content_changed=True
            valid.append((source_id,values))
        if known.keys()-seen:
            content_changed=True
        prepared.append((session,valid,known))
    if content_changed and not source_changed:
        generation=uuid.uuid4()
        source_changed=True
    new_samples=[]; metadata_updates=[]
    for session,rows,known in prepared:
        for source_id,values in rows:
            old=known.get(source_id) if not source_changed else None
            if old is None:
                new_samples.append(TokenSample(session=session,source_generation=generation,source_id=source_id,**values))
            elif (old.effort,old.tier)!=(values['effort'],values['tier']):
                old.effort=values['effort'];old.tier=values['tier']
                metadata_updates.append(old)
    source_state.identity=identity;source_state.generation=generation
    source_state.highwater=highwater;source_state.anchor_digest=anchor
    return source_state,new_samples,metadata_updates,invalid_rows,source_changed


def _source_anchor(connection, sample_id):
    if not sample_id:return ''
    row=connection.execute('SELECT sample_id,session_id,sampled_at,model,cumulative_total_tokens,'
        'cumulative_input_tokens,cumulative_cached_input_tokens,cumulative_output_tokens,'
        'cumulative_reasoning_output_tokens,context_total_tokens,context_window_tokens '
        'FROM usage_samples WHERE sample_id=? ORDER BY rowid DESC LIMIT 1',(sample_id,)).fetchone()
    return hashlib.sha256(json.dumps(list(row) if row else None,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def anchor_at_id(connection, sample_id):
    return _source_anchor(connection,sample_id)


def _source_sample(row):
    names={'source_id':'sample_id','total':'cumulative_total_tokens','input':'cumulative_input_tokens',
        'cached':'cumulative_cached_input_tokens','output':'cumulative_output_tokens',
        'reasoning':'cumulative_reasoning_output_tokens','context':'context_total_tokens','context_limit':'context_window_tokens'}
    values={key:row[column] for key,column in names.items()}
    if any(type(value) is not int or value<0 for value in values.values()):raise ValueError('invalid_source_counter')
    if values['total']!=values['input']+values['output']:raise ValueError('inconsistent_source_total')
    if values['cached']>values['input']:raise ValueError('cached_exceeds_input')
    if values['reasoning']>values['output']:raise ValueError('reasoning_exceeds_output')
    if not isinstance(row['sampled_at'],str):raise ValueError('invalid_source_timestamp')
    stamp=parse_datetime(row['sampled_at'])
    if stamp is None or timezone.is_naive(stamp):raise ValueError('invalid_source_timestamp')
    model=row['model'] or ''
    if not isinstance(model,str):raise ValueError('invalid_source_model')
    values.update(sampled_at=stamp,model=model,
        effort=row['reasoning_effort'] or '' if 'reasoning_effort' in row.keys() else '',
        tier=row['service_tier'] or '' if 'service_tier' in row.keys() else '')
    return values


def summarize(project, session_id=None, samples=None):
    sessions=[]; totals={key:0 for key in ['total','input','cached','output','reasoning']}
    samples = dashboard_samples(project, session_id) if samples is None else samples
    for session, rows, job in samples:
        latest=(max(rows,key=lambda row:row.total) if job else rows[-1]) if rows else None
        resets=sum(b.total<a.total for a,b in zip(rows,rows[1:]))
        if latest:
            for key in totals: totals[key]+=getattr(latest,key)
        history=[]
        for a,b in list(zip(rows,rows[1:]))[-12:]:
            history.append({'at':b.sampled_at,'model':b.model,'total':b.total,'delta':b.total-a.total if b.total>=a.total else None})
        delegated_job = (job.jobs[0] if len(job.jobs)==1 else job) if job else None
        sessions.append({'session':session,'latest':latest,'count':len(rows),'resets':resets,'history':list(reversed(history)),
                         'delegated_job':delegated_job,'direct_job_count':len(job.jobs) if job else 0,
                         'stale':bool(latest and (timezone.now()-latest.sampled_at).total_seconds()>300),
                         'percent':min(100,round(latest.context/latest.context_limit*100,1)) if latest and latest.context_limit else 0})
    return sessions,totals
