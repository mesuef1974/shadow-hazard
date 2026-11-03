"""Read-only historical job measurement; writes an explicit local JSON artifact only."""
import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.utils import timezone
from operations.operator_usage import metrics
from workspace.models import ModelJob


def measure(job_ids=None):
    rows, excluded = [], []
    # Fix the cohort before reading evidence; never dispatch work for measurement.
    query = ModelJob.objects.select_related('charter', 'task_context').order_by('pk')
    if job_ids is not None:
        query = query.filter(pk__in=job_ids)
    jobs = list(query)
    if job_ids is not None and {j.pk for j in jobs} != set(job_ids):
        raise ValueError('Requested cohort contains missing jobs')
    for job in jobs:
        try:
            data = metrics(job)
            proof = data['evidence']
            raw = Path(proof['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != proof['sha256']:
                raise ValueError('Evidence changed during measurement')
            lines = raw.decode('utf-8').splitlines()
            start = json.loads(lines[proof['start_line'] - 1])['payload']['started_at']
            end = json.loads(lines[proof['complete_line'] - 1])['payload']['completed_at']
            if (isinstance(start, bool) or isinstance(end, bool)
                    or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (start, end))
                    or end < start):
                raise ValueError('Invalid turn timestamps')
            dispatch = job.session_dispatch
            rows.append({**data, 'charter': job.charter.key, 'kind': job.task_context.kind,
                'created_at': job.created_at.isoformat(), 'wall_seconds': end - start,
                'attempt_no': dispatch.attempt_no, 'escalation_count': dispatch.escalation_count,
                'quality': 'not_independently_scored', 'execution_state': job.state})
        except Exception as exc:
            excluded.append({'job_id': job.pk, 'state': job.state,
                'reason': str(exc)[:240], 'error_type': type(exc).__name__})
    groups = {}
    for row in rows:
        key = '|'.join(row[k] for k in ('charter', 'kind', 'model', 'thinking'))
        groups.setdefault(key, []).append(row)
    summaries = []
    for key, group in groups.items():
        summary = {'group': key, 'n': len(group), 'job_ids': [r['job_id'] for r in group]}
        for name, values in [('total_tokens', [r['usage']['total_tokens'] for r in group]),
                             ('wall_seconds', [r['wall_seconds'] for r in group])]:
            summary[name] = {'min': min(values), 'median': statistics.median(values), 'max': max(values)}
        summaries.append(summary)
    return {'measured_at': timezone.now().isoformat(), 'cohort_job_ids': [j.pk for j in jobs],
        'basis': 'verified per-turn counters cross-checked against cumulative differences',
        'time_basis': 'task_started to task_complete; includes tools and waiting, not model latency',
        'limitations': ['Historical heterogeneous tasks, not a controlled before/after comparison',
            'Completion is not independent quality acceptance',
            'Excluded evidence is not zero consumption; totals are incomplete',
            'Attempt number does not include unlinked manual retries',
            'Cached input is a subset of input; reasoning is a subset of output'],
        'rows': rows, 'excluded': excluded, 'groups': summaries}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--job', type=int, nargs='+', help='Explicit cohort; missing IDs fail closed.')
    args = parser.parse_args()
    report = measure(args.job)
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
    print(json.dumps({'verified': len(report['rows']), 'excluded': len(report['excluded']),
        'groups': report['groups']}, ensure_ascii=False))
