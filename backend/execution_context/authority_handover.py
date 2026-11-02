"""Prepare an auditable, inert owner-channel transfer plan. This module never applies it."""
import hashlib
import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from workspace.models import (SessionCharter, SessionRenewal, AutoRenewalPolicy,
    AutoRenewalLatch, ModelJob, Project, ProjectDocumentation)
from .models import SessionDispatch, SessionDispatchEvent, AuthorityTransferJournal
from .owner_tools import load_config
from workspace.memory import document
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def monitor_images(project_id):
    from operations.models import OperatorMonitorPolicy
    return [{'policy_id':p.pk,'charter_id':p.charter_id,'owner_id':p.owner_id,'enabled':p.enabled,
        'receipt_id':p.activation_receipt_id,'receipt_digest':p.activation_receipt.digest if p.activation_receipt_id else None,
        'scopes':p.scopes,'cooldown_seconds':p.cooldown_seconds}
        for p in OperatorMonitorPolicy.objects.filter(project_id=project_id).select_related('activation_receipt').order_by('pk')]


def _channel(config):
    identity=config.get('owner_identity') or {}
    return {'caller_thread_id':config['caller_thread_id'],'project_id':config['project_id'],
        'authorizing_user_id':config['authorizing_user_id'],'server_sha256':config['server_sha256'],
        'owner_image_sha256':identity.get('sha256'),'pipe_sha256':digest(config['pipe']),
        'full_config_sha256':digest(config)}


def _blockers(source,target):
    from workspace.renewal_history import retired_review_dispatch_ids
    project_charters=SessionCharter.objects.filter(project_id=source.project_id)
    retired=retired_review_dispatch_ids(source.project_id)
    dispatches=SessionDispatch.objects.filter(target_charter__in=project_charters,
        state__in=['queued','claimed','sent','acknowledged','uncertain']).exclude(pk__in=retired)
    intents=SessionDispatchEvent.objects.filter(dispatch__in=dispatches,state='owner_call_intent')
    renewals=SessionRenewal.objects.filter(charter__project_id=source.project_id).exclude(state__in=['cancelled','rolled_back','switched'])
    latches=AutoRenewalLatch.objects.filter(charter__project_id=source.project_id,state__in=['claimed','owner_intent','ambiguous','candidate_known','verified'])
    # Completed/review/quarantined historical rows are not active blockers.
    # Unresolved dispatches and ambiguous owner intents remain explicit blockers.
    jobs=ModelJob.objects.filter(charter__project_id=source.project_id,state__in=['queued','running','waiting_delivery','uncertain'])
    return {'retired_quarantined_dispatch_ids':retired,
        'active_dispatch_ids':list(dispatches.order_by('pk').values_list('pk',flat=True)),
        'owner_intent_event_ids':list(intents.order_by('pk').values_list('pk',flat=True)),
        'unresolved_renewal_ids':list(renewals.order_by('pk').values_list('pk',flat=True)),
        'active_latch_ids':list(latches.order_by('pk').values_list('pk',flat=True)),
        'active_job_ids':list(jobs.order_by('pk').values_list('pk',flat=True)),
        'live_idle_attestation':'not_performed; database state cannot prove desktop thread idleness'}


def prepare_transfer(owner,target_charter_id):
    if not owner.is_active or owner.role!='supervisor':
        raise PermissionDenied('Active delegated supervisor required')
    config=load_config()
    source=SessionCharter.objects.filter(project_id=config['project_id'],thread_id=config['caller_thread_id']).first()
    if not source or SessionCharter.objects.filter(project_id=config['project_id'],thread_id=config['caller_thread_id']).count()!=1:
        raise ValidationError('Current configured caller must be a registered project charter')
    with transaction.atomic():
        # SQLite acquires its write reservation before any snapshot reads; value is unchanged.
        if Project.objects.filter(pk=source.project_id).update(name=F('name')) != 1:
            raise ValidationError('Project changed before preparation')
        return _prepare_transfer_locked(owner,target_charter_id,config)


def _prepare_transfer_locked(owner,target_charter_id,config):
    source=SessionCharter.objects.filter(project_id=config['project_id'],thread_id=config['caller_thread_id']).select_related('project','policy').first()
    if not source or not source.policy.enabled or SessionCharter.objects.filter(project_id=config['project_id'],thread_id=config['caller_thread_id']).count()!=1:
        raise ValidationError('Current configured caller must be an enabled registered project charter')
    target=SessionCharter.objects.filter(pk=target_charter_id).select_related('project','policy').first()
    if not target or target.key not in {'coordination','session_operator'} or target.project_id!=source.project_id or not target.thread_id or not target.policy.enabled:
        raise ValidationError('Target must be registered coordination or session_operator in the same project')
    if target.pk==source.pk or target.thread_id==source.thread_id:
        raise ValidationError('Source and target must be distinct current registered threads')
    if config['authorizing_user_id']!=owner.pk:
        raise PermissionDenied('Caller channel is not delegated to this supervisor')
    charters=list(SessionCharter.objects.filter(project=source.project,
        key__in=['backend','coordination','frontend','review','session_operator']).select_related('policy').order_by('key'))
    charter_snapshot=[{'id':c.pk,'key':c.key,'thread_id':c.thread_id,'state':c.state,'scope_sha256':digest(c.scope),
        'rules_sha256':digest(c.rules),'policy_id':c.policy_id,'policy_enabled':c.policy.enabled,
        'policy_model':c.policy.model,'policy_thinking':c.policy.thinking,
        'policy_sha256':digest({'model':c.policy.model,'thinking':c.policy.thinking,'enabled':c.policy.enabled})} for c in charters]
    policy_keys=['backend','coordination','frontend','review','session_operator']
    monitor_snapshot=monitor_images(source.project_id)
    policies=list(AutoRenewalPolicy.objects.filter(charter__project=source.project,
        charter__key__in=policy_keys).select_related('charter','owner','activation_receipt').order_by('charter__key'))
    policy_snapshot=[{'id':p.pk,'charter_id':p.charter_id,'charter_key':p.charter.key,'enabled':p.enabled,
        'owner_id':p.owner_id,'activation_receipt_id':p.activation_receipt_id,
        'activation_receipt_digest':p.activation_receipt.digest if p.activation_receipt_id else None,
        'updated_at':p.updated_at.isoformat()} for p in policies]
    renewals=list(SessionRenewal.objects.filter(charter__project=source.project,
        charter__key__in=policy_keys).select_related('charter','approval').order_by('pk'))
    approval_snapshot=[{'renewal_id':r.pk,'charter_id':r.charter_id,'source_thread_id':r.source_thread_id,
        'state':r.state,'approval_id':r.approval_id,'approval_digest':r.approval.digest if r.approval_id else None,
        'create_attempted':r.create_attempted} for r in renewals]
    channel=_channel(config)
    proposed_config=dict(config);proposed_config['caller_thread_id']=target.thread_id
    proposed_config_sha=digest(proposed_config)
    policies_digest=digest({'charters':charter_snapshot,'auto_renewal_policies':policy_snapshot,'renewal_approvals':approval_snapshot})
    channel_digest=digest(channel)
    blockers=_blockers(source,target)
    approval_transfer_plan=[]
    suspension_plan=None
    backend_renewal_plan=None
    for policy in policies:
        if policy.charter_id!=target.pk and policy.enabled and policy.activation_receipt_id:
            approval_transfer_plan.append({'charter_id':policy.charter_id,'charter_key':policy.charter.key,
                'owner_id':policy.owner_id,'old_activation_receipt_id':policy.activation_receipt_id,
                'old_activation_receipt_digest':policy.activation_receipt.digest,
                'required_action':'issue a new owner-signed activation receipt bound to the post-transfer caller channel and same charter/policy/default; then CAS the policy receipt'})
        if policy.charter_id==target.pk and policy.enabled:
            suspension_plan={'charter_id':policy.charter_id,'policy_id':policy.pk,'owner_id':policy.owner_id,
                'reason':'target becomes the caller itself; dynamic self-renewal guard forbids renewal of its own caller thread',
                'required_action':'suspend renewal policy with a receipt before changing caller binding'}
    fingerprint=digest({'source_id':source.pk,'target_id':target.pk,'project_id':source.project_id,
        'channel_digest':channel_digest,'proposed_config_sha256':proposed_config_sha,
        'policies_digest':policies_digest,'operator_monitor':monitor_snapshot,'snapshot_version':2,'blockers':blockers})
    evidence={'status':'authority_transfer_prepared_only','fingerprint':fingerprint,
        'snapshot_version':2,'operator_monitor':monitor_snapshot,
        'execution_enabled':False,'source':{'charter_id':source.pk,'key':source.key,'thread_id':source.thread_id},
        'target':{'charter_id':target.pk,'key':target.key,'thread_id':target.thread_id},
        'project':{'id':source.project_id,'name':source.project.name},'owner_id':owner.pk,
        'channel':channel,'channel_sha256':channel_digest,'proposed_config_sha256':proposed_config_sha,'charters':charter_snapshot,
        'renewal_policies_and_approvals':{'items':policy_snapshot+approval_snapshot,'sha256':policies_digest,
            'renewal_reissue_plan':approval_transfer_plan,
            'frontend_review_reissue_plan':[p for p in approval_transfer_plan if p['charter_key'] in {'frontend','review'}],
            'coordination_suspension_plan':suspension_plan,'caller_suspension_plan':suspension_plan,
            'backend_renewal_plan':backend_renewal_plan},
        'blockers':blockers,'blockers_clear':False,
        'steps':['Prove source and target are both idle through the existing trusted read-only channel.',
            'Confirm no queued/claimed/sent/uncertain dispatch, owner intent, unresolved renewal or latch.',
            'Obtain explicit scoped owner authorization for channel transfer and renewal approvals bound to caller fingerprint.',
            'Re-read all snapshots and compare fingerprint under CAS immediately before any separately authorized transfer.',
            'Transfer channel with atomic compare-and-swap; then update authorized renewal approvals and verify each binding.'],
        'cas_plan':'Compare current config digest and all charter/policy/approval fingerprints; abort on any mismatch.',
        'rollback_plan':'Restore old channel and approval bindings only with fresh CAS and verified old owner channel; otherwise quarantine for human review.',
        'actual_transfer':'blocked; this preparation has no execution path',
        'secrets':'No pipe, node, server paths, credentials or connector configuration copied; pipe represented by SHA-256.'}
    summary=f'خطة تحضير غير مفعلة لنقل صلاحية قناة {source.key} إلى {target.key}؛ لا تثبت خمول الجلسات ولا تنفذ النقل.'
    task_key='authority-transfer:'+fingerprint
    # `document` verifies both existing and newly inserted rows against this exact payload.
    receipt=document(source.project,source.key,task_key,summary,evidence)
    AuthorityTransferJournal.objects.get_or_create(key=fingerprint,defaults={
        'project':source.project,'source':source,'target':target,'owner':owner,'preparation':receipt,
        'before_snapshot':{'source_thread_id':source.thread_id,'target_thread_id':target.thread_id,
            'channel':channel,'charters':charter_snapshot,'policies_digest':policies_digest,'operator_monitor':monitor_snapshot,
            'policies':[{'policy_id':p['id'],'enabled':p['enabled'],'receipt_id':p['activation_receipt_id']} for p in policy_snapshot],
            'blockers':blockers},'old_config_sha256':channel['full_config_sha256'],
        'new_config_sha256':proposed_config_sha,'state':'prepared','execution_enabled':False})
    return receipt


def assert_transfer_not_frozen(project_id):
    """Fail closed around existing RPC paths during a future journaled transfer."""
    if AuthorityTransferJournal.objects.filter(project_id=project_id,state__in=['frozen','config_written','ambiguous']).exists():
        raise ValidationError('Owner channel is frozen by an unresolved authority-transfer journal')
