"""Queue into the existing desktop session using the installed public CLI.

Queue acceptance is not execution. Rollout evidence alone closes the request.
"""
import io
import json
import subprocess
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import SessionDispatch, SessionDispatchEvent, SessionDispatchTurn
from .desktop_session_bridge import _binary, _command, BridgeError
from .rollout_usage import find_rollout, read_turn_evidence, RolloutEvidenceError, has_bound_task_message


def observe_arrivals():
    """Acknowledge only a task marker in an actual user item within its turn."""
    for dispatch in SessionDispatch.objects.filter(state='sent').select_related('job'):
        if not dispatch.claimed_at:continue
        try:
            rows=[json.loads(line) for line in find_rollout(dispatch.target_thread_id).read_text(encoding='utf-8').splitlines()]
            starts=[i for i,r in enumerate(rows) if r.get('type')=='event_msg' and r.get('payload',{}).get('type')=='task_started']
            matches=[]
            marker=dispatch.job.task_context.manifest['task_marker']
            for offset,index in enumerate(starts):
                start=rows[index]['payload']
                if float(start.get('started_at',0)) < int(dispatch.claimed_at.timestamp()):continue
                segment=rows[index:starts[offset+1] if offset+1<len(starts) else len(rows)]
                contexts=[r['payload'] for r in segment if r.get('type')=='turn_context' and r.get('payload',{}).get('turn_id')==start.get('turn_id')]
                if contexts and len({(c.get('model'),c.get('effort')) for c in contexts})==1 and has_bound_task_message(list(enumerate(segment)),dispatch.target_thread_id,start.get('turn_id'),marker):
                    matches.append((start,contexts[0]))
            if len(matches)!=1:continue
            start,context=matches[0]
            detail={'turn_id':start['turn_id'],'model':context.get('model',''),'thinking':context.get('effort',''),
                'started_at':start['started_at'],'source':'incoming user task marker and turn_context'}
            with transaction.atomic():
                _command('acknowledged',dispatch.pk,dispatch.claim_token,turn_id=start['turn_id'],detail=detail)
                SessionDispatchEvent.objects.get_or_create(dispatch=dispatch,state='turn_started',detail=detail)
                from datetime import datetime,timezone as utc
                type(dispatch.job).objects.filter(pk=dispatch.job_id).update(observed_model=detail['model'],
                    started_at=datetime.fromtimestamp(float(start['started_at']),utc.utc),execution_thread_id=dispatch.target_thread_id)
        except (RolloutEvidenceError,OSError,ValueError,TypeError,KeyError,CommandError,IntegrityError):
            continue


def finalize_verified(dispatch,evidence):
    """Quarantine one rejected proof without terminating unrelated deliveries."""
    try:
        if dispatch.state == 'sent':
            _command('acknowledged', dispatch.pk, dispatch.claim_token,
                turn_id=evidence['turn_id'], detail={'transport':'codex queue','evidence':'verified rollout'})
        if 'ESCALATION_REQUEST:' in evidence['result']:
            reason=evidence['result'].split('ESCALATION_REQUEST:',1)[1].strip().splitlines()
            _command('stuck',dispatch.pk,dispatch.claim_token,turn_id=evidence['turn_id'],
                detail={'reason':reason[0][:300] if reason else 'تعثر موثق'})
        else:
            _command('complete-from-rollout',dispatch.pk,dispatch.claim_token,
                turn_id=evidence['turn_id'],detail={'transport':'codex queue'})
    except (CommandError,IntegrityError,ValueError) as exc:
        with transaction.atomic():
            detail={'error_type':type(exc).__name__,'reason':str(exc)[:300],'turn_id':evidence['turn_id']}
            SessionDispatchEvent.objects.get_or_create(dispatch=dispatch,state='verification_failed',detail=detail)
            type(dispatch.job).objects.filter(pk=dispatch.job_id,state__in=['waiting_delivery','running']).update(state='needs_review',
                outcome_note='رفض توثيق دليل التنفيذ؛ حُفظ العائق ولم يُعد إرسال المهمة.')
        return False
    return True


@transaction.atomic
def record_policy_mismatch(dispatch,evidence):
    """Retain real usage once, but never mark a policy-violating turn successful."""
    if dispatch.state=='sent':
        _command('acknowledged',dispatch.pk,dispatch.claim_token,turn_id=evidence['turn_id'],
            detail={'transport':'codex queue','evidence':'completed rollout with policy mismatch'})
    turn,created=SessionDispatchTurn.objects.get_or_create(target_thread_id=dispatch.target_thread_id,
        turn_key=evidence['turn_id'],defaults={'dispatch':dispatch,'model':evidence['model'],
            'thinking':evidence['thinking'],'usage':evidence['usage'],'source_evidence':evidence['evidence']})
    if turn.dispatch_id!=dispatch.pk or turn.usage!=evidence['usage']:
        raise IntegrityError('Turn evidence already belongs to a different execution')
    from workspace.memory import document
    receipt=document(dispatch.target_charter.project,dispatch.target_charter.key,
        f'dispatch:{dispatch.pk}:policy-mismatch:{turn.turn_key}',
        'انتهى الدور لكن النموذج أو التفكير خالف السياسة؛ الاستهلاك الفعلي موثق دون اعتماد نجاح المهمة.',
        {'turn_id':turn.turn_key,'expected_model':dispatch.requested_model,'expected_thinking':dispatch.requested_thinking,
            'observed_model':turn.model,'observed_thinking':turn.thinking,'usage':turn.usage,'source':turn.source_evidence})
    detail={'turn_id':turn.turn_key,'receipt':receipt.pk,'expected_model':dispatch.requested_model,
        'observed_model':turn.model,'expected_thinking':dispatch.requested_thinking,'observed_thinking':turn.thinking}
    SessionDispatchEvent.objects.get_or_create(dispatch=dispatch,state='policy_mismatch',detail=detail)
    dispatch.turn_key=turn.turn_key;dispatch.save(update_fields=['turn_key','updated_at'])
    type(dispatch.job).objects.filter(pk=dispatch.job_id,state__in=['waiting_delivery','running','needs_review']).update(state='needs_review',finished_at=timezone.now(),
        observed_model=turn.model,token_usage=turn.usage,result=evidence['result'],
        outcome_note='اكتمل الدور بنموذج/تفكير مخالف للسياسة؛ الاستهلاك الفعلي محفوظ مرة واحدة، والقبول يحتاج مراجعة.')


def reconcile():
    for dispatch in SessionDispatch.objects.filter(state__in=['sent', 'acknowledged'],
            job__state__in=['waiting_delivery','running']).select_related('job'):
        if not dispatch.claimed_at:
            continue
        try:
            path = find_rollout(dispatch.target_thread_id)
            turns=[]
            records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            for row in records:
                payload=row.get('payload') or {}
                if row.get('type')=='event_msg' and payload.get('type')=='task_complete':
                    if float(payload.get('completed_at',0)) >= dispatch.claimed_at.timestamp():
                        turns.append(payload.get('turn_id'))
            verified=[]
            marker=dispatch.job.task_context.manifest['task_marker']
            bound_turns=[turn for turn in set(turns) if turn and has_bound_task_message(list(enumerate(records)),dispatch.target_thread_id,turn,marker)]
            if len(bound_turns)>1:
                detail={'reason':'multiple completed turns contain the same task marker','turn_ids':sorted(bound_turns)}
                SessionDispatchEvent.objects.get_or_create(dispatch=dispatch,state='evidence_rejected',detail=detail)
                type(dispatch.job).objects.filter(pk=dispatch.job_id,state__in=['waiting_delivery','running']).update(state='needs_review',outcome_note='علامة المهمة مرتبطة بأكثر من دور؛ حُظر اعتماد نتيجة أو إعادة إرسال آلية.')
                continue
            for turn_id in bound_turns:
                if not turn_id:continue
                marker=dispatch.job.task_context.manifest['task_marker']
                if not has_bound_task_message(list(enumerate(records)),dispatch.target_thread_id,turn_id,marker):continue
                try:
                    verified.append(read_turn_evidence(path, turn_id=turn_id,
                        thread_id=dispatch.target_thread_id, claimed_at=dispatch.claimed_at,
                        acknowledged_at=timezone.now(), expected_model='',
                        expected_thinking='',
                        expected_marker=dispatch.job.task_context.manifest['task_marker'],
                        expected_prompt=dispatch.job.prompt, task_id=dispatch.job_id))
                except RolloutEvidenceError as exc:
                    raw_usage=[r.get('payload',{}).get('turn_token_usage') for r in records
                        if r.get('type')=='token_usage_record' and r.get('payload',{}).get('turn_id')==turn_id]
                    detail={'turn_id':turn_id,'reason':str(exc),'unverified_turn_usage':raw_usage[-1] if raw_usage else None,
                        'source':'bound rollout; rejected by strict counter and lifecycle verification'}
                    with transaction.atomic():
                        SessionDispatchEvent.objects.get_or_create(dispatch=dispatch,state='evidence_rejected',detail=detail)
                        type(dispatch.job).objects.filter(pk=dispatch.job_id,state__in=['waiting_delivery','running']).update(state='needs_review',
                            outcome_note='وصل الدور وانتهى؛ تعذر اعتماد دليله. الأرقام المرصودة غير المعتمدة محفوظة في حدث evidence_rejected، ولم تضف إلى الإجمالي.')
                    continue
            if len(verified)!=1:continue
            evidence=verified[0]
        except (RolloutEvidenceError, OSError, ValueError, TypeError):
            continue  # Pending or unverifiable: never invent completion or resend.
        if (evidence['model'],evidence['thinking'])!=(dispatch.requested_model,dispatch.requested_thinking):
            record_policy_mismatch(dispatch,evidence)
        else:
            finalize_verified(dispatch,evidence)


def deliver_one():
    observe_arrivals()
    reconcile()
    # An uncertain send may already exist at the destination. Require reconciliation.
    if SessionDispatch.objects.filter(state='uncertain').exists():
        return None
    output = io.StringIO()
    call_command('session_outbox', 'claim', consumer='native-cli-queue', enqueue_only=True, stdout=output)
    claimed = json.loads(output.getvalue() or '{}')
    if not claimed:
        return None
    dispatch_id, token = claimed['dispatch_id'], claimed['claim_token']
    try:
        result = subprocess.run([_binary(), 'queue', '--thread', claimed['thread_id'],
            '--message', claimed['prompt']], capture_output=True, text=True, encoding='utf-8', timeout=30)
        if result.returncode:
            raise BridgeError('رفض codex queue الطلب: ' + result.stderr[:300])
        _command('sent', dispatch_id, token, detail={'transport': 'codex queue',
            'receipt': result.stdout[:500], 'semantics': 'queue acceptance; model remains the session model'})
        return dispatch_id
    except Exception as exc:
        _command('uncertain', dispatch_id, token, detail={'transport': 'codex queue', 'reason': str(exc)[:300]})
        raise BridgeError(str(exc)[:300]) from exc
