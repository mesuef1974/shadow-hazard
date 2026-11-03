"""Measure every member of a frozen synthetic cohort without dispatching or DB writes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

spec = importlib.util.spec_from_file_location('baseline', Path(__file__).with_name('measure-token-baseline.py'))
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
from workspace.models import ProjectDocumentation, ModelJob
from token_usage.capacity import verified_document


def score(text, expected):
    def invalid_constant(value):
        raise ValueError('Non-standard JSON constant: ' + value)
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError):
        return {'valid_json_object': False, 'correct_cases': 0, 'exact_match': False}
    valid = isinstance(value, dict)
    return {'valid_json_object': valid,
            'correct_cases': sum(value.get(k) == v for k, v in expected.items()) if valid else 0,
            'exact_match': valid and value == expected}


def score_verified(stored, source, expected):
    if not isinstance(source, str) or stored != source:
        return {'source_match': False, 'valid_json_object': False,
                'correct_cases': None, 'exact_match': False}
    return {'source_match': True, **score(source, expected)}


def dispatch_status(job):
    dispatch = getattr(job, 'session_dispatch', None)
    return {'dispatch_state': dispatch.state if dispatch else None,
            'attempt_no': dispatch.attempt_no if dispatch else None,
            'escalation_count': dispatch.escalation_count if dispatch else None,
            'recorded_turns': dispatch.turns.count() if dispatch else None}


def collect(cohort_id):
    cohort = ProjectDocumentation.objects.get(pk=cohort_id)
    protocol = ProjectDocumentation.objects.get(pk=cohort.evidence['protocol_receipt'])
    if not verified_document(cohort) or not verified_document(protocol):
        raise ValueError('Unverified protocol or cohort')
    config = protocol.evidence
    ids = cohort.evidence['job_ids']
    if len(ids) != config['count'] or len(ids) != len(set(ids)):
        raise ValueError('Cohort size or identities changed')
    measured = baseline.measure(ids)
    verified = {r['job_id']: r for r in measured['rows']}
    exclusions = {r['job_id']: r for r in measured['excluded']}
    rows, context_hashes = [], []
    for job_id in ids:
        job = ModelJob.objects.select_related('task_context', 'session_dispatch').get(pk=job_id)
        if (job.charter_id != config['charter_id'] or job.prompt != config['prompt']
                or hashlib.sha256(job.prompt.encode()).hexdigest() != config['prompt_sha256']
                or job.created_at < protocol.created_at):
            raise ValueError('Task differs from frozen protocol')
        ctx = job.task_context
        context_hash = hashlib.sha256(json.dumps([ctx.kind, ctx.instructions, ctx.charter,
            ctx.skill_text, ctx.skills, ctx.plugins], ensure_ascii=False).encode()).hexdigest()
        context_hashes.append(context_hash)
        item = {'job_id': job_id, 'state': job.state, **dispatch_status(job),
            'context_hash': context_hash, 'quality': None,
            'evidence_status': 'verified' if job_id in verified else 'not_verified'}
        if job_id in verified:
            data = verified[job_id]
            if (data['model'], data['thinking']) != (config['model'], config['thinking']):
                raise ValueError('Measured model or thinking differs from protocol')
            item['metrics'] = data
            start_line = data['evidence']['start_line']
            raw = Path(data['evidence']['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != data['evidence']['sha256']:
                raise ValueError('Evidence changed while calculating queue time')
            start = json.loads(raw.decode().splitlines()[start_line - 1])['payload']['started_at']
            end = json.loads(raw.decode().splitlines()[data['evidence']['complete_line'] - 1])['payload']
            item['quality'] = score_verified(job.result, end.get('last_agent_message'), config['expected'])
            wait = start - job.created_at.timestamp()
            if wait < -1:
                raise ValueError('Turn predates submission')
            item['submission_to_turn_seconds'] = max(0, wait)
            item['quality']['no_recorded_tool_calls'] = data['tool_calls'] == 0
        else:
            item['exclusion'] = exclusions[job_id]
        rows.append(item)
    complete = all(r['evidence_status'] == 'verified' for r in rows)
    summary = {}
    if complete:
        for key, values in [('total_tokens', [r['metrics']['usage']['total_tokens'] for r in rows]),
                            ('wall_seconds', [r['metrics']['wall_seconds'] for r in rows])]:
            summary[key] = {'min': min(values), 'median': statistics.median(values), 'max': max(values)}
    return {'protocol_receipt': protocol.pk, 'cohort_receipt': cohort.pk,
        'complete': complete, 'same_frozen_task_context': len(set(context_hashes)) == 1,
        'rows': rows, 'summary': summary,
        'limitations': ['Synthetic classification only; n=3 is not a budget basis',
            'Conversation history, cache and routing markers are not identical across repeats',
            'All submitted jobs retained; unverified consumption is unknown, never zero',
            'Host documentation is measured separately from model output quality']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.cohort)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'complete': result['complete'], 'states': [(r['job_id'], r['state']) for r in result['rows']],
                      'summary': result['summary']}, ensure_ascii=False))
