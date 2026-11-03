"""Limited current-turn proof for explicitly authorized manual adoption only."""
import hashlib
import json
import os
from pathlib import Path
import uuid
from .rollout_usage import find_rollout, RolloutEvidenceError
from .renewal_rollout import MAX_BYTES


def _read(thread_id):
    uuid.UUID(thread_id)
    path=find_rollout(thread_id)
    active=(Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'sessions').resolve()
    if not path.resolve().is_relative_to(active):
        raise RolloutEvidenceError('Adoption target must remain unarchived')
    before=path.stat()
    if before.st_size>MAX_BYTES:raise RolloutEvidenceError('Adoption source exceeds size bound')
    raw=path.read_bytes();after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or not raw.endswith(b'\n'):
        raise RolloutEvidenceError('Changing or partial rollout; wait for natural completion')
    try:
        records=[(i,json.loads(line)) for i,line in enumerate(raw.decode('utf-8').splitlines(),1)]
        if any(not isinstance(r,dict) or not isinstance(r.get('payload'),dict) for _,r in records):raise ValueError()
    except (ValueError,UnicodeError):
        raise RolloutEvidenceError('Malformed adoption source') from None
    meta=[r['payload'] for _,r in records if r.get('type')=='session_meta']
    if (len(meta)!=1 or meta[0].get('session_id',meta[0].get('id'))!=thread_id
            or any(meta[0].get(k,thread_id)!=thread_id for k in ['id','session_id'])):
        raise RolloutEvidenceError('Unique adoption session identity required')
    starts=[(i,r['payload']) for i,r in records if r.get('type')=='event_msg' and r['payload'].get('type')=='task_started']
    if not starts:raise RolloutEvidenceError('No adoption turn')
    last_line,last=starts[-1];turn=last.get('turn_id')
    uuid.UUID(turn)
    if sum(s.get('turn_id')==turn for _,s in starts)!=1 or last.get('root_turn_id',turn)!=turn:
        raise RolloutEvidenceError('Ambiguous current adoption turn')
    terminals=[(i,r['payload']) for i,r in records if r.get('type')=='event_msg'
               and r['payload'].get('type') in {'task_complete','turn_aborted'}]
    gaps=[]; overlaps=[]; opened={}; reported=set()
    for line,r in records:
        if line>=last_line:break
        if r.get('type')!='event_msg':continue
        p=r['payload'];kind=p.get('type');old_turn=p.get('turn_id')
        if kind=='task_started':
            earlier=[{'turn_id':t,'start_line':opened[t]} for t in opened if t not in reported]
            if earlier:
                overlaps.append({'type':'overlapping_start','line':line,'turn_id':old_turn,'earlier':earlier})
                reported.update(opened)
            opened[old_turn]=line
        elif kind in {'task_complete','turn_aborted'}:
            opened.pop(old_turn,None)
    for line,s in starts[:-1]:
        endings=[i for i,p in terminals if p.get('turn_id')==s.get('turn_id') and line<i<last_line]
        if len(endings)!=1:gaps.append({'turn_id':s.get('turn_id'),'start_line':line,'terminal_count':len(endings)})
    # Historical gaps may not extend to the immediately preceding turn.
    if starts[:-1] and any(g['start_line']==starts[-2][0] for g in gaps):
        raise RolloutEvidenceError('History ambiguity touches the acknowledgement turn')
    return {'records':records,'starts':starts,'meta':meta[0],'path':str(path),
            'sha256':hashlib.sha256(raw).hexdigest(),'latest_turn':turn,'archived':False,
            'historical_gaps':gaps,'historical_overlaps':overlaps,'terminals':terminals}


def history_scope(thread_id):
    data=_read(thread_id)
    return {'ack_turn_id':data['latest_turn'],'historical_gaps':data['historical_gaps'],
            'historical_overlaps':data['historical_overlaps']}


def latest_adoption_snapshot(thread_id):
    data=_read(thread_id);start_line,start=data['starts'][-1]
    endings=[(i,p) for i,p in data['terminals'] if i>start_line]
    if not endings:raise RolloutEvidenceError('Unclosed source turn')
    if len(endings)!=1 or endings[0][1].get('turn_id')!=data['latest_turn'] or endings[0][1].get('type')!='task_complete':
        raise RolloutEvidenceError('Current adoption turn is not uniquely completed')
    end_line,end=endings[0]
    for line,r in data['records']:
        if line>end_line and r.get('type') in {'response_item','turn_context'}:
            raise RolloutEvidenceError('Activity follows adoption acknowledgement')
        if line>end_line and r.get('type')=='event_msg' and r['payload'].get('type') not in {'token_count'}:
            raise RolloutEvidenceError('Activity follows adoption acknowledgement')
    data.update({'completed':endings[0],'terminal_type':'task_complete',
                 'historical_closure_proven':False})
    return data
