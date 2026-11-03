"""Explicit task-stage ledger; no dispatching, inferred acceptance, or billing."""
import hashlib
import json
import math
from pathlib import Path
from workspace.models import ModelJob, ProjectDocumentation
from token_usage.capacity import verified_document
from .operator_usage import metrics

ROLES = {'execution', 'review', 'correction', 'coordination'}
COUNTERS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')


def collect(project, stages, acceptance_receipt_id):
    receipt = ProjectDocumentation.objects.get(pk=acceptance_receipt_id, project=project)
    if not verified_document(receipt):
        raise ValueError('Acceptance reference is not verified')
    if not isinstance(stages, list) or not stages:
        raise ValueError('Explicit stages are required')
    stage_ids, jobs, turns, roles = set(), set(), set(), set()
    rows = []
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) not in (
                {'id', 'role', 'job_id'}, {'id', 'role', 'unknown_reason'}):
            raise ValueError('Each stage needs one job or an explicit unknown reason')
        if (not isinstance(stage['id'], str) or not stage['id'].strip()
                or stage['id'] in stage_ids or stage['role'] not in ROLES):
            raise ValueError('Invalid or duplicate stage')
        stage_ids.add(stage['id']); roles.add(stage['role'])
        row = dict(stage)
        if 'unknown_reason' in stage:
            if not isinstance(stage['unknown_reason'], str) or not stage['unknown_reason'].strip():
                raise ValueError('Unknown cost needs a reason')
            row.update(status='unknown', usage=None)
        else:
            job_id = stage['job_id']
            if type(job_id) is not int or job_id in jobs:
                raise ValueError('Invalid or duplicate job')
            jobs.add(job_id)
            job = ModelJob.objects.get(pk=job_id, charter__project=project)
            try:
                data = metrics(job)
                raw = Path(data['evidence']['path']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != data['evidence']['sha256']:
                    raise ValueError('Source changed during collection')
                lines = raw.decode('utf-8').splitlines()
                begin = json.loads(lines[data['evidence']['start_line'] - 1])['payload']['started_at']
                end = json.loads(lines[data['evidence']['complete_line'] - 1])['payload']
                finish = end['completed_at']
                if (any(type(t) not in (int, float) or not math.isfinite(t) for t in (begin, finish))
                        or finish < begin or end.get('last_agent_message') != job.result):
                    raise ValueError('Invalid duration or output mismatch')
            except (ValueError, OSError, KeyError, IndexError, AttributeError) as exc:
                row.update(status='unknown', usage=None, verification_error=type(exc).__name__)
            else:
                identity = (data['thread_id'], data['turn_id'])
                if identity in turns:
                    raise ValueError('One turn cannot be charged to two stages')
                turns.add(identity)
                row.update(status='verified', usage=data['usage'], evidence=data,
                           turn_seconds=finish-begin)
        rows.append(row)
    if roles != ROLES:
        raise ValueError('Execution, review, correction and coordination must be declared')
    known = [r for r in rows if r['status'] == 'verified']
    partial = {key: sum(r['usage'][key] for r in known) for key in COUNTERS}
    partial['noncached_input_tokens'] = partial['input_tokens'] - partial['cached_input_tokens']
    complete = len(known) == len(rows)
    return {'schema': 'task-stage-cost-v1', 'stages': rows,
            'acceptance_receipt': receipt.pk, 'acceptance_digest': receipt.digest,
            'acceptance_basis': 'referenced receipt; not automatically regraded',
            'coverage': 'complete_declared_stages' if complete else 'partial',
            'verified_subtotal': partial, 'total_task_usage': partial if complete else None,
            'sum_verified_turn_seconds': sum(r['turn_seconds'] for r in known),
            'limitations': ['Declared stage coverage is not proof that no stages were omitted',
                'Turn durations may overlap; sum is not task elapsed time',
                'Compaction inside a turn is already included; do not add it again',
                'This ledger does not compute money or authorize a token budget',
                'Do not sum receipt revisions as separate tasks']}
