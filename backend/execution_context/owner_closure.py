"""Bounded worker-run proof checks for owner deliveries, without sending again."""
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from django.utils import timezone
from .desktop_session_bridge import BridgeError
from .models import SessionDispatch,SessionDispatchEvent


LOADED_BUILD_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def verify_owner_closures():
    from workspace.models import DispatcherRun,DispatcherHeartbeat,ProjectDocumentation
    from token_usage.models import TokenSample
    from .owner_delivery import owner_origin_for
    from .rollout_usage import find_rollout,read_turn_evidence
    run=DispatcherRun.objects.filter(pid=os.getpid(),state='running').order_by('-pk').first()
    if not run or not DispatcherHeartbeat.objects.filter(key='local',pid=os.getpid(),state='running').exists():return {'checked':0,'rejected':[]}
    done=SessionDispatchEvent.objects.filter(state='owner_close_checked',detail__worker_run_id=run.pk,
        detail__check_no=2).values('dispatch_id')
    # A moved/temporarily unavailable rollout must not poison this worker forever.
    # Retry proof checks only, with a cooldown; never retry the original send.
    rejected=SessionDispatchEvent.objects.filter(state='owner_close_rejected',detail__worker_run_id=run.pk,
        created_at__gte=timezone.now()-timedelta(minutes=1)).values('dispatch_id')
    candidates=SessionDispatch.objects.filter(state='completed',events__state='owner_call_accepted').exclude(
        pk__in=done).exclude(pk__in=rejected).select_related('job__charter').distinct().order_by('pk')[:8]
    checked=0
    for dispatch in candidates:
        before=TokenSample.objects.filter(session__external_id=dispatch.target_thread_id).count()
        turn_count=dispatch.turns.count()
        try:
            job=dispatch.job
            if job.state!='completed' or not job.finished_at or not dispatch.completed_at:
                raise BridgeError('حالة الإغلاق غير متسقة')
            proof=read_turn_evidence(find_rollout(dispatch.target_thread_id),thread_id=dispatch.target_thread_id,
                turn_id=dispatch.turn_key,claimed_at=dispatch.claimed_at,acknowledged_at=timezone.now(),
                expected_model=dispatch.requested_model,expected_thinking=dispatch.requested_thinking,
                expected_marker=job.task_context.manifest['task_marker'],expected_owner_origin=owner_origin_for(dispatch))
            turn=dispatch.turns.get(turn_key=proof['turn_id'],target_thread_id=dispatch.target_thread_id)
            if (turn.model!=proof['model'] or turn.thinking!=proof['thinking'] or turn.usage!=proof['usage']
                    or job.observed_model!=proof['model'] or job.token_usage!=proof['usage'] or job.result!=proof['result']):
                raise BridgeError('دليل الإغلاق لا يطابق سجلات التنفيذ')
            event=dispatch.events.filter(state='documentation_verified').order_by('-pk').first()
            receipt=ProjectDocumentation.objects.get(pk=event.detail['receipt'])
            digest=hashlib.sha256(json.dumps({'summary':receipt.summary,'evidence':receipt.evidence},
                sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if (digest!=receipt.digest or receipt.digest!=event.detail['digest'] or receipt.summary!=job.result
                    or receipt.project_id!=job.charter.project_id or receipt.session_key!=dispatch.target_charter.key
                    or receipt.evidence.get('turn_id')!=dispatch.turn_key or receipt.evidence.get('usage')!=job.token_usage):
                raise BridgeError('إيصال التوثيق لا يطابق الإغلاق')
        except Exception as exc:
            SessionDispatchEvent.objects.get_or_create(dispatch=dispatch,state='owner_close_rejected',detail={
                'worker_run_id':run.pk,'worker_pid':os.getpid(),'error_type':type(exc).__name__,
                'check_minute':int(timezone.now().timestamp())//60})
            continue
        number=dispatch.events.filter(state='owner_close_checked',detail__worker_run_id=run.pk).count()+1
        if number>2:continue
        after=TokenSample.objects.filter(session__external_id=dispatch.target_thread_id).count()
        SessionDispatchEvent.objects.get_or_create(dispatch=dispatch,state='owner_close_checked',detail={
            'worker_run_id':run.pk,'worker_pid':os.getpid(),'check_no':number,
            'loaded_build_sha256':LOADED_BUILD_SHA256,'turn_id':proof['turn_id'],
            'source_sha256':proof['evidence']['sha256'],'closure_receipt':receipt.pk,
            'turns_before':turn_count,'turns_after':dispatch.turns.count(),
            'token_samples_before':before,'token_samples_after':after,
            'no_send':True,'sample_write_attempted':False})
        checked+=1
    failures=[]
    for failure in SessionDispatchEvent.objects.filter(state='owner_close_rejected',detail__worker_run_id=run.pk):
        # Preserve rejection history; a later strict check supersedes its warning.
        if not SessionDispatchEvent.objects.filter(dispatch_id=failure.dispatch_id,state='owner_close_checked',
                detail__worker_run_id=run.pk,created_at__gt=failure.created_at).exists():
            failures.append(failure.pk)
    return {'checked':checked,'rejected':failures}
