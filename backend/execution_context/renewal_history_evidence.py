"""Historical bootstrap proof only. Never a live idle or binding attestation."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from .renewal_rollout import MAX_BYTES, _bootstrap_evidence
from .rollout_usage import find_rollout, RolloutEvidenceError


def _prefix(path):
    chunks = []
    records = []
    size = 0
    with path.open('rb') as stream:
        while True:
            line = stream.readline(MAX_BYTES - size + 1)
            if not line:
                raise RolloutEvidenceError('Historical bootstrap completion missing')
            size += len(line)
            if size > MAX_BYTES or not line.endswith(b'\n'):
                raise RolloutEvidenceError('Historical bootstrap partial or oversized')
            try:
                record = json.loads(line.decode('utf-8'))
            except (UnicodeError, ValueError):
                raise RolloutEvidenceError('Malformed historical bootstrap') from None
            if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
                raise RolloutEvidenceError('Unsupported historical record shape')
            chunks.append(line)
            records.append((len(records) + 1, record))
            if record.get('type') == 'event_msg' and record['payload'].get('type') == 'task_complete':
                return b''.join(chunks), records


def historical_bootstrap_evidence(row, thread_id):
    uuid.UUID(thread_id)
    if thread_id != row.target_thread_id:
        raise RolloutEvidenceError('Historical candidate must match recorded target')
    path = find_rollout(thread_id)
    active = (Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions').resolve()
    if not path.resolve().is_relative_to(active):
        raise RolloutEvidenceError('Archived candidate cannot be prepared for recovery')
    raw, records = _prefix(path)
    meta = [r['payload'] for _, r in records if r.get('type') == 'session_meta']
    starts = [(i, r['payload']) for i, r in records if r.get('type') == 'event_msg'
              and r['payload'].get('type') == 'task_started']
    end_line, end_record = records[-1]
    end = end_record['payload']
    if (len(meta) != 1 or meta[0].get('session_id', meta[0].get('id')) != thread_id
            or any(meta[0].get(k, thread_id) != thread_id for k in ['id','session_id'])
            or len(starts) != 1 or starts[0][0] >= end_line
            or end.get('turn_id') != starts[0][1].get('turn_id')):
        raise RolloutEvidenceError('Historical session and first turn must match')
    evidence = _bootstrap_evidence(row, thread_id, {
        'records':records, 'starts':starts, 'completed':(end_line,end), 'meta':meta[0],
        'path':str(path), 'sha256':hashlib.sha256(raw).hexdigest(),
    })
    if _prefix(path)[0] != raw:
        raise RolloutEvidenceError('Historical prefix changed during verification')
    # Distinct schema: cannot masquerade as an operational delivery attestation.
    evidence['prefix_sha256'] = evidence.pop('sha256')
    evidence.update({'proof_scope':'historical_bootstrap_prefix_only',
                     'prefix_bytes':len(raw), 'prefix_end_line':end_line,
                     'live_idle_proven':False, 'current_context_delivery_proven':False})
    return evidence
