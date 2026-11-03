"""Database-only detection; notification uses the normal authorized message outbox."""
import hashlib
import uuid
from datetime import timedelta
from django.db import transaction
from django.db.models import CharField, F, Q, Value
from django.db.models.functions import Cast, Concat
from django.utils import timezone
from execution_context.models import AuthorityTransferJournal, SessionDispatch, SessionRoutingPolicy
from execution_context.owner_tools import load_config
from execution_context.messages import send_message
from token_usage.capacity import verified_document
from workspace.memory import document
from workspace.models import Project, SessionCharter, ModelJob, AutoRenewalLatch
from workspace.models import ProjectDocumentation
from .models import OperatorMonitorPolicy, OperatorIncident

SCOPES=['authority_transfer_stalled','renewal_blocked','delivery_uncertain']

LOCAL_DOCUMENTATION = (
    'حلل الدفعة وفق الميثاق والقواعد المرفقة، دون تحميل تاريخ أو إعادة قراءتهما. '
    'الطرفية فقط؛ لا اكتشاف أدوات أو موصلات أو مهارات. '
    'البادئة من جذر المشروع: .venv\\Scripts\\python.exe backend/manage.py. '
    'عند نقص الدليل: project_memory inspect --session session_operator '
    '--compact --document-ids RECEIPT_IDS مرة واحدة؛ اقرأ الكامل فقط لتفاصيل محذوفة لازمة. '
    'احفظ التحليل الفعلي: project_memory document '
    '--session session_operator --task operator-analysis:JOB_ID --summary "تحليلك الفعلي" '
    '--evidence \'{"job_id":JOB_ID}\' . '
    'الحفظ يعيد القراءة والتحقق: verified=true وreread_verified=true يغنيان عن inspect مكرر. '
    'أعد رقم الإيصال ونتيجة موجزة؛ لا تعديل أو معالجة أو تثبيت. '
    'عند غياب الطرفية أو فشل التحقق صرح بالتعثر.\n'
)


def _notification_proof_channel(policy, evidence):
    """Keep historical delivery proof across explicitly authorized runtime updates.

    Updating a signed app must not rewrite the old proof or silently grant a
    different caller, scope, cooldown, or notification policy its authority.
    """
    ignored={'owner_channel','runtime_refresh','report_title','report_sections'}
    seen=set()
    for _ in range(16):
        refresh=evidence.get('runtime_refresh')
        if refresh is None:
            return evidence.get('owner_channel')
        if (not isinstance(refresh,dict)
                or refresh.get('status')!='same_owner_runtime_refresh_authorized'
                or type(refresh.get('prior_activation_id')) is not int
                or refresh['prior_activation_id'] in seen):
            return None
        seen.add(refresh['prior_activation_id'])
        prior=ProjectDocumentation.objects.filter(pk=refresh['prior_activation_id'],
            project_id=policy.project_id,session_key=policy.charter.key).first()
        if (not prior or not verified_document(prior)
                or prior.digest!=refresh.get('prior_activation_digest')):
            return None
        before=prior.evidence
        old_channel=before.get('owner_channel',{})
        new_channel=evidence.get('owner_channel',{})
        if (not isinstance(old_channel,dict) or not isinstance(new_channel,dict)
                or not old_channel.get('caller_thread_id')
                or old_channel.get('caller_thread_id')!=new_channel.get('caller_thread_id')
                or {k:v for k,v in before.items() if k not in ignored}
                    != {k:v for k,v in evidence.items() if k not in ignored}):
            return None
        evidence=before
    return None


def self_notification_allowed(policy, config):
    """Permit idle self-notification only after a proved official delivery."""
    if not policy.activation_receipt or not verified_document(policy.activation_receipt):return False
    evidence=policy.activation_receipt.evidence
    if (evidence.get('allow_idle_self_notification') is not True
            or type(evidence.get('self_notification_proof_id')) is not int
            or policy.charter.key!='session_operator'
            or config.get('caller_thread_id')!=policy.charter.thread_id):return False
    proof=ProjectDocumentation.objects.filter(pk=evidence.get('self_notification_proof_id'),
        project_id=policy.project_id,session_key=policy.charter.key).first()
    if (not proof or not verified_document(proof) or proof.digest!=evidence.get('self_notification_proof_digest')):return False
    data=proof.evidence
    proof_channel=_notification_proof_channel(policy,evidence)
    if (not proof_channel or data.get('status')!='operator_idle_self_notification_verified'
            or data.get('owner_id')!=policy.owner_id or data.get('owner_channel')!=proof_channel):return False
    dispatch=SessionDispatch.objects.select_related('job').filter(pk=data.get('dispatch_id'),
        target_charter=policy.charter,target_thread_id=policy.charter.thread_id,state='completed',
        turn_key=data.get('turn_id'),job__state='completed',job__owner_id=policy.owner_id).first()
    if not dispatch:return False
    from execution_context.models import SessionMessage
    if not SessionMessage.objects.filter(job=dispatch.job,sender=policy.charter,recipient=policy.charter).exists():return False
    intent=dispatch.events.filter(state='owner_call_intent',
        detail__caller_thread_id=policy.charter.thread_id).order_by('-pk').first()
    turn=dispatch.turns.filter(turn_key=dispatch.turn_key,target_thread_id=policy.charter.thread_id).first()
    completion=ProjectDocumentation.objects.filter(pk=data.get('completion_receipt_id')).first()
    return bool(intent and dispatch.events.filter(state='owner_call_accepted',
            detail__call_id=intent.detail.get('call_id')).exists()
        and turn and turn.model=='gpt-6-luna' and turn.thinking=='low'
        and turn.usage==dispatch.job.token_usage and completion
        and verified_document(completion) and completion.project_id==policy.project_id
        and completion.session_key==policy.charter.key and completion.evidence.get('turn_id')==turn.turn_key
        and completion.evidence.get('usage')==turn.usage
        and dispatch.events.filter(state='documentation_verified',detail__receipt=completion.pk,
            detail__digest=completion.digest).exists())


def authorized_channel(policy, config, evidence):
    pinned=evidence.get('owner_channel',{})
    if not isinstance(pinned,dict):return False
    if (pinned.get('server_sha256')!=config.get('server_sha256')
            or pinned.get('owner_image_sha256')!=config.get('owner_identity',{}).get('sha256')):
        return False
    if pinned.get('caller_thread_id')==config.get('caller_thread_id'):return True
    if evidence.get('follow_verified_owner_handoffs') is not True:return False
    for journal in AuthorityTransferJournal.objects.filter(project_id=policy.project_id,owner_id=policy.owner_id,
            state='complete',after_snapshot__caller_thread_id=config.get('caller_thread_id')).select_related('authorization','preparation'):
        approval=journal.authorization;preparation=journal.preparation
        if (approval and verified_document(approval) and verified_document(preparation)
                and preparation.evidence.get('fingerprint')==journal.key
                and preparation.evidence.get('status')=='authority_transfer_prepared_only'
                and approval.project_id==policy.project_id
                and approval.evidence.get('status')=='authority_transfer_execute_authorized'
                and approval.evidence.get('journal_key')==journal.key
                and approval.evidence.get('fingerprint')==journal.key
                and approval.evidence.get('project_id')==journal.project_id
                and approval.evidence.get('source_id')==journal.source_id
                and approval.evidence.get('target_id')==journal.target_id
                and approval.evidence.get('old_config_sha256')==journal.old_config_sha256
                and approval.evidence.get('owner_id')==policy.owner_id
                and approval.evidence.get('new_config_sha256')==journal.new_config_sha256):return True
    return False


def conditions(policy, now):
    result={}
    if SCOPES[0] in policy.scopes:
        known=OperatorIncident.objects.filter(policy=policy,state='open',
            code__startswith='authority_transfer_').values_list('condition_key',flat=True)
        for row in AuthorityTransferJournal.objects.filter(project_id=policy.project_id,
                state__in=['frozen','config_written','ambiguous']).alias(
                    incident_key=Concat(Value('transfer:'),Cast('pk',output_field=CharField()))).filter(
                    Q(updated_at__lt=now-timedelta(seconds=120))|Q(incident_key__in=known)):
            code='authority_transfer_'+row.state
            result[f'transfer:{row.pk}']=(code,{'journal_id':row.pk,'state':row.state})
    if SCOPES[1] in policy.scopes:
        for row in AutoRenewalLatch.objects.filter(charter__project_id=policy.project_id,
                source_thread_id=F('charter__thread_id'),state__in=['quarantined','ambiguous','blocked']):
            result[f'latch:{row.pk}']=('renewal_'+row.state,{'latch_id':row.pk,'charter_id':row.charter_id,'state':row.state})
    if SCOPES[2] in policy.scopes:
        for row in SessionDispatch.objects.filter(target_charter__project_id=policy.project_id,
                target_thread_id=F('target_charter__thread_id'),state='uncertain'):
            result[f'dispatch:{row.pk}:uncertain']=('delivery_uncertain',{'dispatch_id':row.pk,'job_id':row.job_id})
    return result


def activation_valid(policy, config):
    receipt=policy.activation_receipt
    e=receipt.evidence if receipt and isinstance(receipt.evidence,dict) else {}
    route=SessionRoutingPolicy.objects.filter(category='text',enabled=True).first()
    return bool(policy.enabled and policy.owner.is_active and policy.owner.role=='supervisor'
        and policy.charter.key=='session_operator' and policy.charter.project_id==policy.project_id
        and policy.charter.policy.enabled and policy.charter.policy.model=='gpt-6-luna'
        and policy.charter.policy.thinking=='low' and policy.charter.policy.max_attempts==1
        and route and route.model=='gpt-6-luna' and route.thinking=='low' and route.max_attempts==1 and route.max_escalations==0
        and isinstance(policy.scopes,list) and bool(policy.scopes)
        and set(policy.scopes)<=set(SCOPES) and 120<=policy.cooldown_seconds<=86400
        and receipt and verified_document(receipt) and receipt.project_id==policy.project_id
        and receipt.session_key==policy.charter.key
        and e.get('status')=='operator_monitor_activation_authorized'
        and e.get('policy_id')==policy.pk and e.get('charter_id')==policy.charter_id
        and e.get('owner_id')==policy.owner_id and e.get('scopes')==policy.scopes
        and e.get('cooldown_seconds')==policy.cooldown_seconds
        and e.get('charter_sha256')==hashlib.sha256((policy.charter.scope+'\n'+policy.charter.rules).encode()).hexdigest()
        and config.get('project_id')==policy.project_id and config.get('authorizing_user_id')==policy.owner_id
        and authorized_channel(policy,config,e))


def inspect_policy(policy_id):
    project_id=OperatorMonitorPolicy.objects.values_list('project_id',flat=True).get(pk=policy_id)
    with transaction.atomic():
        # A write reservation serializes this operation on SQLite as well as row-lock databases.
        Project.objects.filter(pk=project_id).update(name=F('name'))
        policy=OperatorMonitorPolicy.objects.select_for_update().select_related('owner','charter__policy').get(pk=policy_id)
        # Keep authority rows locked without applying FOR UPDATE to a nullable join.
        if policy.activation_receipt_id:
            policy.activation_receipt=ProjectDocumentation.objects.select_for_update().get(pk=policy.activation_receipt_id)
        if policy.project_id!=project_id:raise ValueError('Operator project changed during inspection')
        if not policy.enabled:return 0
        config=load_config()
        if not activation_valid(policy,config):
            policy.last_error='activation_invalid';policy.save(update_fields=['last_error']);return 0
        if policy.last_error:
            policy.last_error='';policy.save(update_fields=['last_error'])
        now=timezone.now();current=conditions(policy,now)
        OperatorIncident.objects.filter(policy=policy,state='open').exclude(condition_key__in=current).update(state='resolved',resolved_at=now)
        for key,(code,reference) in current.items():
            previous=OperatorIncident.objects.filter(policy=policy,condition_key=key).order_by('-generation').first()
            if not previous or previous.state=='resolved':
                OperatorIncident.objects.create(policy=policy,condition_key=key,generation=previous.generation+1 if previous else 1,code=code,reference=reference)
            elif previous.code!=code or previous.reference!=reference:
                previous.code=code;previous.reference=reference;previous.save(update_fields=['code','reference'])
        unstarted=ModelJob.objects.filter(charter=policy.charter,state='queued',session_dispatch__isnull=True,
            operatorincident__policy=policy).distinct()
        for job in unstarted:
            if OperatorIncident.objects.filter(policy=policy,job=job,state='open').exists():continue
            job.state='cancelled';job.finished_at=now
            job.outcome_note='زال العائق قبل بدء تحليل المشغّل؛ أُلغي طلب المتابعة دون إرسال أو استهلاك.'
            job.save(update_fields=['state','finished_at','outcome_note'])
            document(policy.project,policy.charter.key,f'operator-auto-cancel:{job.pk}',job.outcome_note,
                {'job_id':job.pk,'all_incidents_resolved':True,'never_dispatched':True})
        pending=list(OperatorIncident.objects.filter(policy=policy,state='open',job__isnull=True).order_by('pk')[:12])
        if not pending:return 0
        previous_jobs=OperatorIncident.objects.filter(policy=policy,job__isnull=False).values_list('job_id',flat=True).distinct()
        for job_id in previous_jobs:
            cancelled=ProjectDocumentation.objects.filter(project_id=policy.project_id,session_key=policy.charter.key,
                task_key=f'operator-auto-cancel:{job_id}').first()
            if (cancelled and verified_document(cancelled)
                    and ModelJob.objects.filter(pk=job_id,state='cancelled',session_dispatch__isnull=True).exists()):continue
            analysis=ProjectDocumentation.objects.filter(project_id=policy.project_id,session_key=policy.charter.key,
                task_key=f'operator-analysis:{job_id}').first()
            if not analysis or not verified_document(analysis):return 0
        sender=SessionCharter.objects.filter(project_id=policy.project_id,thread_id=config['caller_thread_id'],policy__enabled=True).first()
        if not sender or ((sender.pk==policy.charter_id or sender.thread_id==policy.charter.thread_id)
                and not self_notification_allowed(policy,config)):
            policy.last_error='safe_caller_transfer_required';policy.save(update_fields=['last_error']);return 0
        if (policy.last_sent_at and now-policy.last_sent_at<timedelta(seconds=policy.cooldown_seconds)) or ModelJob.objects.filter(
                charter=policy.charter,state__in=['queued','running','waiting_delivery','waiting_readonly','uncertain']).exists():return 0
        from workspace.job_lanes import blocking_dispatches
        if blocking_dispatches().filter(target_charter=policy.charter).exists():return 0
        body=LOCAL_DOCUMENTATION+'استبدل JOB_ID برقم طلبك الفعلي. إذا زال العائق فوثق ذلك دون إجراء.\n'+ '\n'.join(
            f'incident={i.pk} generation={i.generation} code={i.code} refs={i.reference}' for i in pending)
        message=send_message(sender=sender,recipient=policy.charter,owner=policy.owner,body=body,
            message_key=uuid.uuid5(uuid.NAMESPACE_URL,'operator:'+','.join(str(i.pk) for i in pending)),task_kind='text')
        OperatorIncident.objects.filter(pk__in=[i.pk for i in pending]).update(job=message.job)
        document(policy.project,policy.charter.key,f'operator-natural-batch:{message.job_id}',
            'تنبيه طبيعي صادر من فحص المراقب البرمجي، دون تنبيه لقياس الاستهلاك.',
            {'job_id':message.job_id,'policy_id':policy.pk,'incident_ids':[i.pk for i in pending]})
        policy.last_sent_at=now;policy.last_error='';policy.save(update_fields=['last_sent_at','last_error'])
        return 1


def maintenance():
    result={'sent':0,'errors':[]}
    for pk in OperatorMonitorPolicy.objects.filter(enabled=True).values_list('pk',flat=True):
        try:result['sent']+=inspect_policy(pk)
        except Exception as exc:result['errors'].append({'operation':'operator_monitor','error_type':type(exc).__name__})
    return result
