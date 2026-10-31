"""Read-only, turn-scoped usage evidence from a Codex rollout JSONL file."""
import hashlib
import json
import math
import os
from pathlib import Path


class RolloutEvidenceError(ValueError):
    pass


COUNTERS=('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')


def _counters(value):
    if not isinstance(value,dict) or any(not isinstance(value.get(key),int) or value[key]<0 for key in COUNTERS):
        raise RolloutEvidenceError('عدادات الاستهلاك غير مكتملة أو غير صالحة')
    if value['total_tokens']!=value['input_tokens']+value['output_tokens']:
        raise RolloutEvidenceError('إجمالي العداد لا يطابق الإدخال والإخراج')
    if value['cached_input_tokens']>value['input_tokens'] or value['reasoning_output_tokens']>value['output_tokens']:
        raise RolloutEvidenceError('عدادات التوكنز الفرعية تتجاوز الإجمالي')
    return {key:value[key] for key in COUNTERS}


def find_rollout(thread_id, explicit_path=None):
    root=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'sessions'
    if explicit_path:
        path=Path(explicit_path).resolve()
        try:path.relative_to(root.resolve())
        except ValueError:raise RolloutEvidenceError('ملف الدليل يجب أن يكون داخل مجلد sessions الخاص بـ Codex')
        if path.name.endswith(f'-{thread_id}.jsonl') and path.is_file():return path
        raise RolloutEvidenceError('مسار rollout لا يطابق جلسة الوجهة')
    matches=list(root.glob(f'**/*-{thread_id}.jsonl'))
    if len(matches)!=1:raise RolloutEvidenceError('تعذر تحديد ملف rollout وحيد للجلسة المستهدفة')
    return matches[0]


def has_bound_task_message(records,thread_id,turn_id,marker):
    """Require the host's explicit user-item thread/turn binding, not a quoted marker."""
    if not marker:return False
    for _,record in records:
        payload=record.get('payload') or {}
        item=payload.get('item') or {}
        if (record.get('type')=='event_msg' and payload.get('type')=='item_completed'
            and payload.get('thread_id')==thread_id and payload.get('turn_id')==turn_id
            and str(item.get('type','')).lower()=='usermessage'
            and marker in json.dumps(item.get('content',[]),ensure_ascii=False)):
            return True
    return False


def read_turn_evidence(path, *, thread_id, claimed_at, acknowledged_at, expected_model, expected_thinking, turn_id='', expected_marker='', expected_prompt='', task_id=None):
    path=Path(path)
    records=[]
    try:
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        for line_no,line in enumerate(raw.decode('utf-8').splitlines(),1):
            try:record=json.loads(line)
            except (json.JSONDecodeError,UnicodeError):raise RolloutEvidenceError(f'سجل JSONL تالف عند السطر {line_no}')
            if isinstance(record,dict):records.append((line_no,record))
    except OSError as exc:raise RolloutEvidenceError('تعذر قراءة ملف rollout') from exc

    starts={};contexts={};completes={};usage_records=[];snapshots=[];session_ids=[]
    for line_no,record in records:
        payload=record.get('payload') or {}
        if record.get('type')=='session_meta':session_ids.append(payload.get('session_id'))
        if record.get('type')=='event_msg':
            typ=payload.get('type');key=payload.get('turn_id')
            if typ=='task_started' and key:starts.setdefault(key,[]).append((line_no,payload))
            elif typ=='task_complete' and key:completes.setdefault(key,[]).append((line_no,payload))
            elif typ=='token_count':
                info=payload.get('info') or {};total=info.get('total_token_usage')
                if total is not None:snapshots.append((line_no,total))
        elif record.get('type')=='turn_context' and payload.get('turn_id'):
            contexts.setdefault(payload['turn_id'],[]).append((line_no,payload))
        elif record.get('type')=='token_usage_record' and payload.get('turn_id'):
            usage_records.append((line_no,payload))

    if session_ids!=[thread_id]:raise RolloutEvidenceError('هوية session_meta لا تطابق المحادثة المستهدفة أو تكررت')
    if not claimed_at or not acknowledged_at:raise RolloutEvidenceError('نافذة المطالبة والإقرار ناقصة')
    lower=claimed_at.timestamp();upper=acknowledged_at.timestamp()
    candidates=[]
    for key,entries in starts.items():
        if turn_id and key!=turn_id:continue
        context_rows=contexts.get(key,[])
        if len(entries)!=1 or not context_rows or len(completes.get(key,[]))!=1:continue
        if len({(row.get('model'),row.get('effort')) for _,row in context_rows})!=1:continue
        start_line,start=entries[0];ctx_line,ctx=contexts[key][0];end_line,end=completes[key][0]
        try:start_at=float(start['started_at'])
        except (KeyError,TypeError,ValueError):continue
        # Desktop timestamps can be whole seconds, while Django preserves
        # microseconds. Compare at source precision; task binding is still required.
        source_lower=math.floor(lower) if isinstance(start['started_at'],int) else lower
        if not source_lower<=start_at<=upper:continue
        if not start_line<ctx_line<end_line:continue
        candidates.append((key,start_line,ctx_line,end_line,start,ctx,end))
    if len(candidates)!=1:raise RolloutEvidenceError('يلزم دور واحد مكتمل داخل نافذة المطالبة والإقرار')
    key,start_line,ctx_line,end_line,start,ctx,end=candidates[0]
    if expected_marker or expected_prompt:
        previous_end=max([line for entries in completes.values() for line,_ in entries if line<start_line] or [0])
        incoming=[]
        def strings(value):
            if isinstance(value,str):return [value]
            if isinstance(value,list):return [text for item in value for text in strings(item)]
            if isinstance(value,dict):return [text for item in value.values() for text in strings(item)]
            return []
        for line,record in records:
            payload=record.get('payload',{})
            trusted=(payload.get('role')=='user' or
                (payload.get('type')=='function_call_output' and payload.get('name')=='send_message_to_thread' and payload.get('namespace')=='codex_app'))
            if previous_end<line<end_line and record.get('type')=='response_item' and trusted:incoming.extend(strings(payload))
        if expected_marker:
            bound=has_bound_task_message([(line,row) for line,row in records if start_line<line<end_line],thread_id,key,expected_marker)
        else:
            labels=(f'رقم الطلب {task_id}',f'مهمة سفيان رقم {task_id}')
            bound=any(expected_prompt in text and any(label in text for label in labels) for text in incoming)
        if not bound:raise RolloutEvidenceError('رسالة المهمة لا تطابق علامة المحاولة أو النص ورقم الطلب القديم')
    if expected_model and ctx.get('model')!=expected_model:raise RolloutEvidenceError('نموذج الدور لا يطابق نموذج التوجيه')
    if expected_thinking and ctx.get('effort')!=expected_thinking:raise RolloutEvidenceError('مستوى التفكير لا يطابق سياسة التوجيه')
    if end.get('turn_id')!=key:raise RolloutEvidenceError('حدث نهاية الدور لا يطابق معرف الدور')
    if 'completed_at' not in end or 'started_at' not in end:raise RolloutEvidenceError('توقيتات بداية/نهاية الدور ناقصة')
    try:
        if float(end['completed_at'])<float(end['started_at']):raise RolloutEvidenceError('توقيت نهاية الدور يسبق بدايته')
    except (TypeError,ValueError):raise RolloutEvidenceError('توقيت نهاية الدور غير صالح')
    overlapping=[turn for turn,rows in starts.items() if turn!=key and any(start_line<line<end_line for line,_ in rows)]
    if overlapping:raise RolloutEvidenceError('وُجد دور متداخل في حدود المهمة؛ لا يمكن نسبة الاستهلاك بأمان')

    before=[(line,value) for line,value in snapshots if line<start_line]
    after=[(line,value) for line,value in snapshots if start_line<line<end_line]
    if not before or not after:raise RolloutEvidenceError('عينة تراكمية قبل الدور أو بعده مفقودة')
    before_line,before_value=before[-1];after_line,after_value=after[-1]
    before_counts=_counters(before_value);after_counts=_counters(after_value)
    delta={}
    interior=sorted((line,value) for line,value in snapshots if before_line<=line<=after_line)
    previous=before_counts
    for _,value in interior[1:]:
        current=_counters(value)
        if any(current[field]<previous[field] for field in COUNTERS):raise RolloutEvidenceError('عداد الجلسة أعيد ضبطه داخل حدود الدور')
        previous=current
    for field in COUNTERS:delta[field]=after_counts[field]-before_counts[field]
    turn_rows=[(line,row) for line,row in usage_records if row.get('turn_id')==key and line<end_line]
    if not turn_rows:raise RolloutEvidenceError('سجل استهلاك الدور المباشر مفقود')
    previous_turn=None
    for line,row in turn_rows:
        if row.get('thread_id')!=thread_id or not start_line<line<end_line:raise RolloutEvidenceError('سجل دور خارج حدود الجلسة')
        counts=_counters(row.get('turn_token_usage'))
        if previous_turn and any(counts[key]<previous_turn[key] for key in COUNTERS):raise RolloutEvidenceError('إعادة ضبط داخل عدادات الدور')
        previous_turn=counts
    usage_line,usage_row=turn_rows[-1]
    if usage_row.get('thread_id')!=thread_id:raise RolloutEvidenceError('سجل الاستهلاك لا يخص جلسة الوجهة')
    if not start_line<ctx_line<usage_line<after_line<end_line:raise RolloutEvidenceError('ترتيب حدود الدور أو عداداته غير صالح')
    direct=_counters(usage_row.get('turn_token_usage') or usage_row.get('usage'))
    if direct!=delta:raise RolloutEvidenceError('استهلاك الدور لا يطابق فرق العداد التراكمي')
    thread_after=_counters(usage_row.get('thread_token_usage'))
    if thread_after!=after_counts:
        # Codex can retain different historical baselines in its two counters.
        # Accept only when both independently measured deltas match this turn.
        prior=[(line,row) for line,row in usage_records if line<start_line and row.get('thread_id')==thread_id]
        if not prior:raise RolloutEvidenceError('اختلاف أساس العدادات بلا دليل سابق للمقارنة')
        thread_before=_counters(prior[-1][1].get('thread_token_usage'))
        if {field:thread_after[field]-thread_before[field] for field in COUNTERS}!=delta:
            raise RolloutEvidenceError('فرق عداد الجلسة المباشر لا يطابق استهلاك الدور')
        previous=thread_before
        for _,row in turn_rows:
            current=_counters(row.get('thread_token_usage'))
            if any(current[field]<previous[field] for field in COUNTERS):
                raise RolloutEvidenceError('إعادة ضبط داخل عداد الجلسة المباشر')
            previous=current
    result=end.get('last_agent_message')
    if not isinstance(result,str) or not result.strip():raise RolloutEvidenceError('الرد النهائي للدور غير موجود')
    return {'turn_id':key,'model':ctx['model'],'thinking':ctx['effort'],'usage':delta,'result':result[:40000],
        'evidence':{'path':str(path),'sha256':digest,'start_line':start_line,'turn_context_line':ctx_line,
            'usage_record_line':usage_line,'before_snapshot_line':before_line,'after_snapshot_line':after_line,
            'complete_line':end_line,'basis':'turn_token_usage cross-checked against cumulative delta'}}
