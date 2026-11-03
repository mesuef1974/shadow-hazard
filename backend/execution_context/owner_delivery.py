"""Deliver through the official owner MCP tool; persist intent before one send."""
import io
import hashlib
import json
import os
from pathlib import Path
import uuid
from datetime import timedelta
from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from workspace.models import SessionCharter
from .models import SessionDispatch,SessionDispatchEvent,SessionRoutingPolicy,TaskContext
from .owner_tools import OwnerTools,OwnerToolsError,load_config
from .owner_channel import ensure_channel
from .desktop_session_bridge import BridgeError,_command,_release_claim,_mark_uncertain


def owner_origin_for(dispatch):
    event=dispatch.events.filter(state='owner_call_intent').order_by('-pk').first()
    if not event:return None
    detail=event.detail
    if not isinstance(detail,dict):raise BridgeError('نية إرسال المالك غير صالحة')
    for key in ('call_id','context_id','caller_thread_id'):
        try:uuid.UUID(detail[key])
        except (KeyError,ValueError,TypeError) as exc:raise BridgeError('معرف نية المالك غير صالح') from exc
    return {'thread_id':detail['caller_thread_id'],'context_id':detail['context_id'],'call_id':detail['call_id']}


def _idle(client,thread_id,context_id):
    data=client.call('read_thread',{'threadId':thread_id,'turnLimit':1,'includeOutputs':False,
        'maxOutputCharsPerItem':50},call_id=str(uuid.uuid4()),context_id=context_id)
    thread=data.get('thread',{})
    if (thread.get('id')!=thread_id or thread.get('kind')!='codex' or thread.get('hostId')!='local'
            or Path(thread.get('cwd','')).resolve()!=settings.BASE_DIR.parent.resolve()):
        raise OwnerToolsError('هوية أو نطاق جلسة المالك غير مطابق')
    status=thread.get('status',{})
    turns=data.get('turns',[])
    return (status.get('type') in {'idle','notLoaded'} and not status.get('activeFlags')
            and (status.get('type')=='idle' or bool(turns))
            and (not turns or turns[0].get('status')=='completed'))


CONTEXT_FIELDS=('kind','skills','plugins','instructions','charter','skill_text','manifest')


def _final_authority(candidate,claimed,config,context_snapshot):
    """Called inside a short write transaction, immediately before durable intent."""
    from .routing import validate_manifest
    from workspace.models import Project
    from django.db.models import F
    from .authority_handover import assert_transfer_not_frozen
    Project.objects.filter(pk=config['project_id']).update(name=F('name'))
    try:assert_transfer_not_frozen(config['project_id'])
    except Exception as exc:raise BridgeError('نقل صلاحية المالك جارٍ؛ لا نية إرسال جديدة') from exc
    if load_config()!=config:raise BridgeError('تغير إعداد أو هوية موصل المالك قبل الإرسال')
    changed=SessionDispatch.objects.filter(pk=candidate.pk,state='claimed',claim_token=claimed['claim_token']).update(updated_at=timezone.now())
    if changed!=1:raise BridgeError('سُحب حجز الإرسال أو تغيرت هويته')
    fresh=SessionDispatch.objects.select_for_update().select_related(
        'job__owner','job__charter','target_charter__policy').get(pk=candidate.pk)
    # Keep the optional policy locked without a nullable FOR UPDATE join.
    if fresh.routing_policy_id:
        fresh.routing_policy=SessionRoutingPolicy.objects.select_for_update().get(pk=fresh.routing_policy_id)
    job=fresh.job;target=fresh.target_charter
    from workspace.model_limits import model_limit_error
    limit_error=model_limit_error(target,fresh.requested_model,fresh.requested_thinking)
    if limit_error:
        raise BridgeError(limit_error)
    caller=SessionCharter.objects.filter(project_id=config['project_id'],thread_id=config['caller_thread_id'],
        policy__enabled=True,state__in=['active','prepared','ready_waiting_for_task']).first()
    if (not caller or job.owner_id!=config['authorizing_user_id'] or not job.owner.is_active
            or job.owner.role!='supervisor' or job.state!='running' or job.mode!='workspace-write'
            or job.charter.project_id!=config['project_id'] or target.project_id!=config['project_id']
            or job.charter_id!=candidate.job.charter_id or not job.charter.policy.enabled
            or fresh.job_id!=candidate.job_id or fresh.target_charter_id!=candidate.target_charter_id
            or not target.policy.enabled or target.state not in {'active','prepared','ready_waiting_for_task'}
            or target.thread_id!=fresh.target_thread_id or fresh.target_thread_id!=claimed['thread_id']
            or fresh.sent_at or fresh.acknowledged_at or fresh.turns.filter(turn_key=fresh.turn_key).exists()
            or not fresh.routing_policy or not fresh.routing_policy.enabled):
        raise BridgeError('سُحب التفويض أو تغير نطاق المهمة قبل الإرسال')
    plan=TaskContext.objects.select_related('job__charter__project').get(job=job)
    if {name:getattr(plan,name) for name in CONTEXT_FIELDS}!=context_snapshot:
        raise BridgeError('تغير سياق المهمة بعد المطالبة')
    validate_manifest(plan)
    manifest=plan.manifest;policy=manifest.get('policy_snapshot',{})
    if (not policy or policy!={name:getattr(fresh.routing_policy,name) for name in policy}
            or manifest.get('job_snapshot')!={'prompt':job.prompt,'mode':job.mode}
            or manifest.get('target_snapshot')!={'thread_id':target.thread_id,'scope':target.scope,'rules':target.rules}
            or not 1<=fresh.attempt_no<=fresh.max_attempts):
        raise BridgeError('تغيرت لقطة السياسة أو الميثاق أو المهمة')
    from workspace.model_limits import execution_pair
    expected=execution_pair(manifest,fresh.attempt_no)
    if ((fresh.requested_model,fresh.requested_thinking)!=expected
            or (job.requested_model,job.requested_thinking)!=expected
            or (claimed['model'],claimed['thinking'])!=expected
            or claimed['attempt_no']!=fresh.attempt_no):
        raise BridgeError('تغير نموذج المحاولة أو التفكير قبل الإرسال')
    if fresh.events.filter(state='owner_call_intent',detail__attempt_no=fresh.attempt_no).exists():
        raise BridgeError('المحاولة لها نية إرسال سابقة؛ لا إعادة تلقائية')
    return fresh


def recover_unstarted_claims():
    """Called only while the startup worker lock is held, before sending.

    Release only owner claims with no durable send boundary and an idle target.
    Unknown, active, or possibly sent work is left untouched for quarantine.
    """
    pending=SessionDispatch.objects.filter(state='claimed',claimed_by='codex-owner-mcp',
        sent_at__isnull=True,acknowledged_at__isnull=True).exclude(claim_token=None)
    if not pending.exists():return []
    try:
        config=ensure_channel(load_config())
        released=[]
        with OwnerTools(config) as client:
            for item in pending.select_related('job__owner')[:8]:
                def safe(row):
                    return (row.state=='claimed' and row.claim_token==item.claim_token
                        and row.target_charter.project_id==config['project_id']
                        and row.job.owner_id==config['authorizing_user_id']
                        and row.job.owner.is_active and row.job.owner.role=='supervisor'
                        and not row.turns.exists() and not row.turn_key
                        and not row.events.filter(state__in=['owner_call_intent','owner_call_accepted',
                            'sent','acknowledged','turn_started']).exists())
                if not safe(item) or not _idle(client,item.target_thread_id,str(uuid.uuid4())):continue
                with transaction.atomic():
                    fresh=SessionDispatch.objects.select_for_update().select_related('job__owner','target_charter').get(pk=item.pk)
                    if not safe(fresh) or fresh.sent_at or fresh.acknowledged_at:continue
                    _release_claim(item.pk,item.claim_token,'استعادة حجز غير مرسل بعد إعادة تشغيل العامل؛ ثبُت خمول الجلسة')
                    SessionDispatchEvent.objects.create(dispatch=fresh,state='unstarted_claim_recovered',
                        detail={'no_send':True,'idle_verified':True,'no_send_intent':True,'worker_pid':os.getpid()})
                    released.append(item.pk)
        return released
    except (OwnerToolsError,BridgeError):
        return []


def deliver_one():
    if not SessionDispatch.objects.filter(state='queued',job__mode='workspace-write').exists():return None
    try:config=load_config()
    except OwnerToolsError as exc:raise BridgeError('موصل المالك غير مهيأ؛ لا إرسال بديل') from exc
    caller=SessionCharter.objects.filter(project_id=config['project_id'],thread_id=config['caller_thread_id'],
        policy__enabled=True,state__in=['active','prepared','ready_waiting_for_task']).first()
    if not caller:raise BridgeError('جلسة المالك أو مشروع الموصل غير صالح')
    from .models import SessionWriterRecovery
    from workspace.job_lanes import available_dispatches
    recent_busy_threads=SessionDispatchEvent.objects.filter(state='deferred_busy',
        detail__reason='target_active_before_claim',created_at__gte=timezone.now()-timedelta(seconds=30)
        ).values('dispatch__target_thread_id')
    recovering_threads=SessionWriterRecovery.objects.filter(state__in=['archiving','restoring','uncertain']).values('thread_id')
    candidates=available_dispatches(SessionDispatch.objects.filter(state='queued',job__mode='workspace-write',
        job__charter__project_id=config['project_id'],target_charter__project_id=config['project_id'])
        .filter(job__charter__policy__enabled=True,target_charter__policy__enabled=True,
            target_charter__state__in=['active','prepared','ready_waiting_for_task'],routing_policy__enabled=True)
        .exclude(target_thread_id__in=recovering_threads).exclude(target_thread_id__in=recent_busy_threads)
        .select_related('job__owner','target_charter')).order_by('created_at','pk')
    candidate_batch=[]
    thread_ids=candidates.order_by().values_list('target_thread_id',flat=True).distinct()
    for thread_id in thread_ids.iterator():
        item=candidates.filter(target_thread_id=thread_id).order_by('created_at','pk').first()
        if item:candidate_batch.append(item)
    candidate_batch.sort(key=lambda item:(item.created_at,item.pk))
    candidate_batch=candidate_batch[:8]
    if not candidate_batch:return None
    # Renewal is read-only and strictly before any claim or durable send intent.
    previous=config
    from .authority_handover import assert_transfer_not_frozen
    try:assert_transfer_not_frozen(config['project_id'])
    except Exception as exc:raise BridgeError('قناة المالك مجمدة لنقل صلاحية؛ لا مطالبة أو إرسال') from exc
    try:config=ensure_channel(config)
    except OwnerToolsError as exc:raise BridgeError('تعذر التحقق من قناة المالك؛ لا حجز ولا إرسال') from exc
    context_id='';candidate=None
    rejected=[]
    try:
        with OwnerTools(config) as probe_client:
            for item in candidate_batch:
                if (item.job.owner_id!=config['authorizing_user_id'] or not item.job.owner.is_active
                        or item.job.owner.role!='supervisor'):
                    rejected.append(item)
                    if not item.events.filter(state='owner_auth_rejected',detail__reason='owner_authorization_changed_before_claim').exists():
                        SessionDispatchEvent.objects.create(dispatch=item,state='owner_auth_rejected',detail={
                            'before_send':True,'reason':'owner_authorization_changed_before_claim'})
                    continue
                probe_context=str(uuid.uuid4())
                idle=_idle(probe_client,item.target_thread_id,probe_context)
                if idle:
                    candidate=item;context_id=probe_context;break
                SessionDispatchEvent.objects.create(dispatch=item,state='deferred_busy',detail={
                    'reason':'target_active_before_claim','no_claim':True,'retry_after_seconds':30})
    except Exception as exc:
        raise BridgeError('تعذر التحقق من جاهزية الجلسة قبل المطالبة') from exc
    if not candidate:
        if rejected:raise BridgeError('تفويض المشرف لم يعد صالحًا لبعض المهام؛ لم يُرسل شيء')
        return None
    if config!=previous:
        SessionDispatchEvent.objects.create(dispatch=candidate,state='owner_channel_renewed',detail={
            'old_endpoint_sha256':hashlib.sha256(previous['pipe'].encode()).hexdigest(),
            'new_endpoint_sha256':hashlib.sha256(config['pipe'].encode()).hexdigest(),
            'image_sha256':config['owner_identity']['sha256'],
            'read_verified':True,'before_claim':True,'no_send':True,'worker_pid':os.getpid()})
    call_id=str(uuid.uuid4());claimed=None;intent=False;stage='owner_client'
    try:
        with OwnerTools(config) as client:
            with transaction.atomic():
                SessionDispatch.objects.filter(pk=candidate.pk,state='queued').update(updated_at=timezone.now())
                output=io.StringIO()
                stage='claim'
                call_command('session_outbox','claim',dispatch=candidate.pk,consumer='codex-owner-mcp',stdout=output)
                stage='claim_response'
                value=json.loads(output.getvalue() or '{}')
                if not value:return None
                if value['dispatch_id']!=candidate.pk:raise BridgeError('تغيرت وجهة المطالبة؛ لم يرسل الموصل')
                claimed=value
                context_snapshot=TaskContext.objects.filter(job_id=candidate.job_id).values(*CONTEXT_FIELDS).get()
            if not _idle(client,claimed['thread_id'],context_id):
                _release_claim(candidate.pk,claimed['claim_token'],'جلسة المالك أصبحت نشطة؛ لم تُرسل الرسالة')
                return None
            # This durable event is a no-replay boundary, not a saved Codex turn credential.
            with transaction.atomic():
                _final_authority(candidate,claimed,config,context_snapshot)
                SessionDispatchEvent.objects.create(dispatch_id=candidate.pk,state='owner_call_intent',detail={
                    'call_id':call_id,'context_id':context_id,'caller_thread_id':config['caller_thread_id'],
                    'tool':'send_message_to_thread','server_sha256':config['server_sha256'],
                    'authorized_owner_id':candidate.job.owner_id,'project_id':config['project_id'],
                    'attempt_no':claimed['attempt_no'],
                    'payload_sha256':hashlib.sha256(json.dumps({key:claimed[key] for key in ('thread_id','prompt','model','thinking')},sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                    'worker_pid':os.getpid(),'context_source':'generated_per_dispatch',
                    'exported_pipe_in_worker_environment':bool(os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'))})
            intent=True
            receipt=client.call('send_message_to_thread',{'threadId':claimed['thread_id'],'prompt':claimed['prompt'],
                'model':claimed['model'],'thinking':claimed['thinking']},call_id=call_id,context_id=context_id)
            if receipt.get('threadId')!=claimed['thread_id']:raise OwnerToolsError('إيصال إرسال المالك لا يطابق الوجهة')
            SessionDispatchEvent.objects.create(dispatch_id=candidate.pk,state='owner_call_accepted',detail={
                'call_id':call_id,'context_id':context_id,'thread_id':receipt['threadId']})
            _command('sent',candidate.pk,claimed['claim_token'],detail={
                'transport':'codex-owner-mcp','thread_id':claimed['thread_id'],
                'call_id':call_id,'context_id':context_id,'model':claimed['model'],'thinking':claimed['thinking']})
            from .audit import record_owner_delivery
            record_owner_delivery(candidate,config,call_id,context_id)
            return candidate.pk
    except Exception as exc:
        if not claimed:
            # This runs after the claim transaction rolled back. Preserve safe
            # diagnostics on the actual candidate, without logging prompts or secrets.
            SessionDispatchEvent.objects.create(dispatch_id=candidate.pk,state='owner_preclaim_error',
                detail={'stage':stage,'error_type':type(exc).__name__,
                        'before_send':True,'no_send_intent':True})
        if claimed:
            if intent:
                _mark_uncertain(candidate.pk,claimed['claim_token'],'إرسال المالك ملتبس؛ لا إعادة تلقائية')
            else:
                SessionDispatchEvent.objects.create(dispatch_id=candidate.pk,state='owner_auth_rejected',
                    detail={'before_send':True,'error_type':type(exc).__name__})
                if SessionDispatch.objects.filter(pk=candidate.pk,state='claimed',claim_token=claimed['claim_token']).exists():
                    _release_claim(candidate.pk,claimed['claim_token'],'تعذر إثبات تفويض موصل المالك قبل الإرسال')
        raise BridgeError('تعذر موصل المالك؛ راجع الحدث المحفوظ دون إعادة إرسال ملتبس') from exc
