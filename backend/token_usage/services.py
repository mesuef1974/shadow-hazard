import sqlite3
from pathlib import Path
from django.conf import settings
from django.db import transaction, DatabaseError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import TrackedSession, TokenSample, SyncRun, CollectorState


def synchronize():
    run = SyncRun.objects.create()
    added = 0
    try:
        path = Path(settings.TOKEN_USAGE_SOURCE).resolve()
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'usage_collector_runs' in tables:
                collector=connection.execute('SELECT status,started_at,completed_at,heartbeat_at,source_files_updated,samples_inserted FROM usage_collector_runs ORDER BY run_id DESC LIMIT 1').fetchone()
                last_success=connection.execute("SELECT completed_at FROM usage_collector_runs WHERE status='success' ORDER BY run_id DESC LIMIT 1").fetchone()
                weekly=connection.execute('SELECT sampled_at,recorded_at,weekly_used_percent,weekly_remaining_percent,weekly_window_minutes,weekly_resets_at FROM usage_samples WHERE weekly_used_percent IS NOT NULL AND weekly_window_minutes=10080 ORDER BY sampled_at DESC,sample_id DESC LIMIT 1').fetchone()
                collector_data=dict(collector) if collector else {}
                collector_data['last_success_at']=last_success['completed_at'] if last_success else None
                CollectorState.objects.update_or_create(key='local',defaults={'payload':{'collector':collector_data,'weekly':dict(weekly) if weekly else {}}})
            with transaction.atomic():
                # Acquire the SQLite write reservation before reading destination rows.
                # This serializes a manual sync with the local watcher without lock upgrades.
                SyncRun.objects.filter(pk=run.pk).update(message='المزامنة قيد التنفيذ')
                for session in TrackedSession.objects.filter(enabled=True):
                    known_metadata={source_id:(effort,tier) for source_id,effort,tier in session.samples.values_list('source_id','effort','tier')}
                    rows = connection.execute('SELECT * FROM usage_samples WHERE session_id=? ORDER BY sampled_at,sample_id', (session.external_id,))
                    for row in rows:
                        metadata={'effort':row['reasoning_effort'] or '' if 'reasoning_effort' in row.keys() else '', 'tier':row['service_tier'] or '' if 'service_tier' in row.keys() else ''}
                        if row['sample_id'] in known_metadata:
                            if known_metadata[row['sample_id']]!=(metadata['effort'],metadata['tier']):
                                session.samples.filter(source_id=row['sample_id']).update(**metadata)
                            continue
                        for key in ['sample_id','cumulative_total_tokens','cumulative_input_tokens','cumulative_cached_input_tokens','cumulative_output_tokens','cumulative_reasoning_output_tokens','context_total_tokens','context_window_tokens']:
                            if not isinstance(row[key],int) or row[key]<0: raise ValueError('Invalid source counter')
                        if not isinstance(row['sampled_at'],str):raise ValueError('Invalid source timestamp')
                        stamp = parse_datetime(row['sampled_at'])
                        if stamp is None or timezone.is_naive(stamp):
                            raise ValueError('Invalid source timestamp')
                        _, created = TokenSample.objects.get_or_create(session=session,source_id=row['sample_id'],defaults={
                            'sampled_at':stamp,'model':row['model'] or '',**metadata,
                            'total':row['cumulative_total_tokens'],'input':row['cumulative_input_tokens'],
                            'cached':row['cumulative_cached_input_tokens'],'output':row['cumulative_output_tokens'],
                            'reasoning':row['cumulative_reasoning_output_tokens'],'context':row['context_total_tokens'],
                            'context_limit':row['context_window_tokens'],
                        })
                        added += created
        finally:
            connection.close()
        run.success=True; run.added=added;run.message='اكتملت مزامنة الجلسات المحددة للمشروع.'
    except (sqlite3.Error, OSError, ValueError, KeyError, IndexError, TypeError, DatabaseError):
        run.message='تعذرت قراءة مصدر الاستهلاك المحلي؛ آخر بيانات محفوظة ما زالت متاحة.'
    finally:
        run.finished_at=timezone.now();run.save()
    return run


def summarize(project, session_id=None):
    sessions=[]; totals={key:0 for key in ['total','input','cached','output','reasoning']}
    selected=TrackedSession.objects.filter(project=project)
    if session_id:selected=selected.filter(pk=session_id)
    for session in selected:
        rows=list(session.samples.all())
        latest=rows[-1] if rows else None
        resets=sum(b.total<a.total for a,b in zip(rows,rows[1:]))
        if latest:
            for key in totals: totals[key]+=getattr(latest,key)
        history=[]
        for a,b in list(zip(rows,rows[1:]))[-12:]:
            history.append({'at':b.sampled_at,'model':b.model,'total':b.total,'delta':b.total-a.total if b.total>=a.total else None})
        sessions.append({'session':session,'latest':latest,'count':len(rows),'resets':resets,'history':list(reversed(history)),
                         'stale':bool(latest and (timezone.now()-latest.sampled_at).total_seconds()>300),
                         'percent':min(100,round(latest.context/latest.context_limit*100,1)) if latest and latest.context_limit else 0})
    return sessions,totals
