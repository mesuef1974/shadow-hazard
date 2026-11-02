"""Independent, bounded creation-bootstrap evidence. No usage writes or RPCs."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid
from django.utils import timezone
from .rollout_usage import find_rollout, RolloutEvidenceError, COUNTERS

MAX_BYTES = 64 * 1024 * 1024


def snapshot(thread_id):
    uuid.UUID(thread_id)
    path = find_rollout(thread_id)
    active = (Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions').resolve()
    if not path.resolve().is_relative_to(active):
        raise RolloutEvidenceError('Archived rollout cannot be adopted for renewal')
    before = path.stat()
    if before.st_size > MAX_BYTES:raise RolloutEvidenceError('Rollout exceeds bounded verification size')
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or not raw.endswith(b'\n'):
        raise RolloutEvidenceError('Changing or partial rollout; wait for natural completion')
    try:
        records = [(i, json.loads(line)) for i, line in enumerate(raw.decode('utf-8').splitlines(), 1)]
    except (ValueError, UnicodeError):
        raise RolloutEvidenceError('Malformed rollout; no partial acceptance') from None
    if any(not isinstance(r, dict) or not isinstance(r.get('payload'), dict) for _, r in records):
        raise RolloutEvidenceError('Unsupported rollout record shape')
    sessions = [r.get('payload', {}) for _, r in records if r.get('type') == 'session_meta']
    if (len(sessions) != 1 or sessions[0].get('session_id', sessions[0].get('id')) != thread_id
            or any(sessions[0].get(k, thread_id) != thread_id for k in ['id', 'session_id'])):
        raise RolloutEvidenceError('Unique matching session metadata required')
    starts = [(i, r['payload']) for i, r in records if r.get('type') == 'event_msg'
              and r.get('payload', {}).get('type') == 'task_started']
    if not starts:raise RolloutEvidenceError('No completed turn evidence')
    active = None;seen = set();active_start = None;aborted_at = None
    for line, record in records:
        p=record.get('payload', {})
        if record.get('type')!='event_msg':continue
        if p.get('type')=='task_started':
            turn=p.get('turn_id')
            if not isinstance(turn,str) or turn in seen or active is not None:
                raise RolloutEvidenceError('Overlapping or duplicated source turns')
            if aborted_at is not None:
                started = p.get('started_at')
                if (type(started) not in (int, float) or not math.isfinite(started)
                        or started < aborted_at):
                    raise RolloutEvidenceError('Next turn predates aborted source closure')
                aborted_at=None
            seen.add(turn);active=turn;active_start=p
        elif p.get('type')=='turn_aborted':
            if not active or p.get('turn_id')!=active:
                raise RolloutEvidenceError('Conflicting or unmatched aborted source turn')
            try:
                uuid.UUID(active)
                started = p['started_at']; completed = p['completed_at']
                if (any(type(value) not in (int, float) or not math.isfinite(value)
                        for value in [started, completed, active_start.get('started_at')])
                        or started != active_start['started_at']
                        or not 0 <= started <= completed <= timezone.now().timestamp()
                        or p.get('root_turn_id', active) != active_start.get('root_turn_id', active)):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise RolloutEvidenceError('Invalid aborted source identity or time bounds') from None
            active=None;active_start=None;aborted_at=completed
        elif p.get('type')=='task_complete':
            if not active or p.get('turn_id')!=active:
                raise RolloutEvidenceError('Conflicting or unmatched completed source turn')
            active=None;active_start=None
    if active is not None:raise RolloutEvidenceError('Unclosed source turn')
    latest = starts[-1][1]['turn_id']
    finished = [(i, r['payload']) for i, r in records if r.get('type') == 'event_msg'
        and r.get('payload', {}).get('type') == 'task_complete' and r['payload'].get('turn_id') == latest]
    if len(finished) != 1 or finished[0][0] <= starts[-1][0]:
        raise RolloutEvidenceError('Latest source turn is active or ambiguous')
    return {'records': records, 'starts': starts, 'completed': finished[0], 'meta': sessions[0],
            'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'latest_turn': latest}


def _texts(content):
    return [part.get('text') for part in content if isinstance(part, dict) and part.get('type') in {'text', 'input_text'}]


def _assistant_text(content, text_type):
    if (not isinstance(content, list) or not content or any(not isinstance(part, dict)
            or part.get('type') != text_type or not isinstance(part.get('text'), str) for part in content)):
        raise RolloutEvidenceError('Plain assistant completion text required')
    return ''.join(part['text'] for part in content)


def bootstrap_evidence(row, thread_id):
    data = snapshot(thread_id)
    if len(data['starts']) != 1 or data['meta'].get('cwd') != row.context['cwd']:
        raise RolloutEvidenceError('Candidate is not a fresh, same-project bootstrap')
    intent = row.events.filter(state='owner_create_intent').select_related('receipt').get().receipt
    from token_usage.capacity import verified_document
    if not verified_document(intent):raise RolloutEvidenceError('Create authority receipt changed')
    authority = intent.evidence
    start_line, start = data['starts'][0]
    end_line, end = data['completed']
    turn_id = str(uuid.UUID(start['turn_id']))
    root = start.get('root_turn_id')
    attribution = start.get('turn_attribution', {})
    if (not isinstance(attribution, dict) or root != turn_id
            or attribution.get('turn_id') != turn_id or attribution.get('root_turn_id') != root
            or attribution.get('turn_trigger') != 'app_tool_create_thread'
            or attribution.get('parent_turn_id') != authority['context_id']
            or end.get('root_turn_id') != root):
        raise RolloutEvidenceError('Bootstrap create parent/root is not bound to the authorized owner call')
    try:
        started = float(start['started_at']); completed = float(end['completed_at'])
        lower = math.floor(intent.created_at.timestamp()) if type(start['started_at']) is int else intent.created_at.timestamp()
        if not lower <= started <= completed <= timezone.now().timestamp():raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RolloutEvidenceError('Invalid bootstrap time bounds') from None
    incoming = []
    pairs = {}
    contexts = []
    usage = []
    assistant_items = []
    assistant_responses = []
    for line, record in data['records']:
        p = record.get('payload', {})
        if not start_line < line < end_line:continue
        if record.get('type') not in {'turn_context', 'response_item', 'event_msg', 'token_usage_record', 'world_state'}:
            raise RolloutEvidenceError('Unsupported bootstrap record cannot prove zero execution')
        if record.get('type') == 'event_msg' and p.get('type') not in {
                'item_completed', 'token_count', 'thread_settings_applied'}:
            raise RolloutEvidenceError('Unexpected bootstrap event cannot prove zero execution')
        if record.get('type') == 'turn_context':
            if p.get('turn_id') != turn_id:
                raise RolloutEvidenceError('Conflicting bootstrap context turn')
            contexts.append(p)
        if record.get('type') == 'token_usage_record':usage.append((line, p))
        if record.get('type') == 'response_item':
            if p.get('type') not in {'message', 'reasoning', 'additional_tools', 'function_call_output'}:
                raise RolloutEvidenceError('Bootstrap execution or unsupported response item is forbidden')
            if p.get('type') == 'message' and p.get('role') == 'assistant':
                assistant_responses.append((line, p))
            if p.get('type') == 'function_call_output' and (
                    p.get('name') != 'create_thread' or p.get('namespace') != 'codex_app'):
                raise RolloutEvidenceError('Only the owner bootstrap delivery output is allowed')
        if record.get('type') == 'response_item' and p.get('type') == 'function_call_output':
            if p.get('name') == 'create_thread' and p.get('namespace') == 'codex_app':
                if not p.get('id') or p['id'] in pairs:
                    raise RolloutEvidenceError('Duplicate owner delivery item')
                pairs[p.get('id')] = p.get('output')
        if record.get('type') != 'event_msg' or p.get('type') != 'item_completed':continue
        item = p.get('item', {})
        if item.get('type') not in {'UserMessage', 'FunctionCallOutput', 'AgentMessage', 'Reasoning'}:
            raise RolloutEvidenceError('Bootstrap execution or unsupported host item is forbidden')
        if p.get('thread_id') != thread_id or p.get('turn_id') != turn_id:
            raise RolloutEvidenceError('Bootstrap host item has conflicting attribution')
        if item.get('type') == 'AgentMessage':assistant_items.append((line, item))
        if item.get('type') == 'FunctionCallOutput' and (
                item.get('name') != 'create_thread' or item.get('namespace') != 'codex_app'):
            raise RolloutEvidenceError('Unexpected bootstrap tool output')
        if item.get('type') == 'UserMessage' and _texts(item.get('content', [])) == [row.bootstrap_prompt]:
            incoming.append(line)
        if (item.get('type') == 'FunctionCallOutput' and item.get('name') == 'create_thread'
                and item.get('namespace') == 'codex_app' and item.get('id') in pairs
                and item.get('output') == pairs[item['id']] and isinstance(item.get('output'), str)):
            match = re.fullmatch(r'\s*<codex_delegation>\s*<source_thread_id>([0-9a-f-]{36})</source_thread_id>\s*<input>([\s\S]*)</input>\s*</codex_delegation>\s*', item['output'])
            if match and match.group(1) == authority['caller_thread_id'] and match.group(2) == row.bootstrap_prompt:
                incoming.append(line)
            else:
                raise RolloutEvidenceError('Owner bootstrap delivery text/source does not match')
        elif item.get('type') == 'FunctionCallOutput':
            raise RolloutEvidenceError('Unpaired owner bootstrap delivery')
    if (len(incoming) != 1 or len(pairs) > 1 or not contexts
            or len({(c.get('model'), c.get('effort')) for c in contexts}) != 1):
        raise RolloutEvidenceError('Exact, host-bound bootstrap and unambiguous model context required')
    if any(not isinstance(c.get('model'), str) or not c['model'] or c.get('effort') not in
           {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'} for c in contexts):
        raise RolloutEvidenceError('Effective model/thinking evidence missing')
    if any(c.get('root_turn_id') != root for c in contexts):
        raise RolloutEvidenceError('Context owner root mismatch')
    approval=row.approval
    if not approval or not verified_document(approval):
        raise RolloutEvidenceError('Default inheritance approval not verified')
    expected_default=approval.evidence.get('expected_desktop_default')
    actual_default={'model':contexts[0]['model'],'thinking':contexts[0]['effort']}
    if (approval.evidence.get('desktop_default_inheritance_authorized') is not True
            or expected_default!=actual_default):
        raise RolloutEvidenceError('Actual model/thinking differs from approved Desktop default')
    # Completion proves delivery, not semantic obedience. The marker is diagnostic;
    # the approved contract requires a paired final response and no execution.
    finals = [(line, item) for line, item in assistant_items if item.get('phase') == 'final_answer']
    responses = [(line, item) for line, item in assistant_responses if item.get('phase') == 'final_answer']
    if len(finals) != 1 or len(responses) != 1:
        raise RolloutEvidenceError('Unique paired final assistant completion required')
    final_line, final = finals[0]; response_line, response = responses[0]
    final_text = _assistant_text(final.get('content'), 'Text')
    if (not final_text.strip() or not final.get('id') or response.get('id') != final['id']
            or response.get('status') != 'completed'
            or response_line <= final_line or final_line <= incoming[0]
            or assistant_items[-1][0] != final_line or assistant_responses[-1][0] != response_line
            or _assistant_text(response.get('content'), 'output_text') != final_text
            or end.get('last_agent_message') != final_text
            or not usage or incoming[0] >= usage[0][0]):
        raise RolloutEvidenceError('Final host/response/turn completion does not agree')
    total = {field: 0 for field in COUNTERS}; seen = set()
    for line, record in usage:
        counts = record.get('usage', {})
        if (record.get('thread_id') != thread_id or record.get('turn_id') != turn_id
                or record.get('root_turn_id') != root or not record.get('response_id')
                or record['response_id'] in seen or any(type(counts.get(k)) is not int or counts[k] < 0 for k in COUNTERS)):
            raise RolloutEvidenceError('Invalid or duplicate response usage')
        if (counts['total_tokens'] != counts['input_tokens'] + counts['output_tokens']
                or counts['cached_input_tokens'] > counts['input_tokens']
                or counts['reasoning_output_tokens'] > counts['output_tokens']):
            raise RolloutEvidenceError('Inconsistent original usage')
        seen.add(record['response_id'])
        total = {field: total[field] + counts[field] for field in COUNTERS}
        aggregate=record.get('turn_token_usage', {})
        if (any(type(aggregate.get(k)) is not int for k in COUNTERS) or aggregate != total):
            raise RolloutEvidenceError('Response sum differs from turn usage')
    return {'thread_id': thread_id, 'turn_id': turn_id, 'source_row': usage[-1][0],
        'sha256': data['sha256'], 'path': data['path'], 'observed_at': timezone.now().isoformat(),
        'input_line': incoming[0], 'complete_line': end_line, 'root_turn_id': root,
        'model': contexts[0]['model'], 'thinking': contexts[0]['effort'], 'usage': total,
        'model_contract':'explicit_desktop_default_inheritance_no_model_parameters_sent',
        'completion_contract':'exact_owner_delivery_paired_final_zero_execution_v2',
        'ack_marker_exact':final_text.strip() == 'CONTEXT_READY',
        'semantic_instruction_compliance_proven':False,
        'execution_tool_calls':0,
        'matches_approved_default':True,
        'matches_charter_model_policy': actual_default=={
            'model':row.context['model_policy']['model'],'thinking':row.context['model_policy']['thinking']},
        'no_usage_write': True}
