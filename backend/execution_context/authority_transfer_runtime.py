"""Fail-closed execute/recover/rollback for a human-authorized channel transfer.

No code in this module creates threads or sends messages. RPC reads happen outside DB
transactions. Production execution is additionally default-off by Django setting.
"""
import hashlib
import json
import uuid
from pathlib import Path
from contextlib import contextmanager
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from workspace.models import (AutoRenewalPolicy, Project, ProjectDocumentation, SessionCharter)
from workspace.memory import document
from .authority_handover import digest, _channel, _blockers, monitor_images
from .models import AuthorityTransferJournal, SessionDispatch
from .owner_tools import OwnerTools, OwnerToolsError, load_config, save_config, config_path
from .owner_channel import pipe_identity
from .renewal_rollout import snapshot as rollout_snapshot


class TransferUncertain(RuntimeError): pass


@contextmanager
def _configuration_lock():
    from workspace.worker_lock import single_worker
    with single_worker(config_path().with_suffix('.lock')) as acquired:
        if not acquired:raise TransferUncertain('Connector configuration is reserved by another writer')
        yield


def _config_sha(config):
    return digest(config)


def _policy_images(project_id, include_operator=False):
    from workspace.models import SessionRenewal
    keys=['backend','coordination','frontend','review']+(['session_operator'] if include_operator else [])
    charters=list(SessionCharter.objects.filter(project_id=project_id,key__in=keys).select_related('policy').order_by('key'))
    charter_image=[{'id':c.pk,'key':c.key,'thread_id':c.thread_id,'state':c.state,'scope_sha256':digest(c.scope),
        'rules_sha256':digest(c.rules),'policy_id':c.policy_id,'policy_enabled':c.policy.enabled,
        'policy_model':c.policy.model,'policy_thinking':c.policy.thinking,
        'policy_sha256':digest({'model':c.policy.model,'thinking':c.policy.thinking,'enabled':c.policy.enabled})} for c in charters]
    policies=list(AutoRenewalPolicy.objects.filter(charter__project_id=project_id,charter__key__in=keys).select_related('charter','activation_receipt').order_by('charter__key'))
    policy_image=[{'id':p.pk,'charter_id':p.charter_id,'charter_key':p.charter.key,'enabled':p.enabled,
        'owner_id':p.owner_id,'activation_receipt_id':p.activation_receipt_id,
        'activation_receipt_digest':p.activation_receipt.digest if p.activation_receipt_id else None,
        'updated_at':p.updated_at.isoformat()} for p in policies]
    renewals=list(SessionRenewal.objects.filter(charter__project_id=project_id,charter__key__in=keys).select_related('charter','approval').order_by('pk'))
    renewal_image=[{'renewal_id':r.pk,'charter_id':r.charter_id,'source_thread_id':r.source_thread_id,
        'state':r.state,'approval_id':r.approval_id,'approval_digest':r.approval.digest if r.approval_id else None,
        'create_attempted':r.create_attempted} for r in renewals]
    return charter_image,policy_image+renewal_image


def _assert_preparation_current(row):
    evidence=row.preparation.evidence
    charter_image,items=_policy_images(row.project_id,include_operator=evidence.get('snapshot_version',1)>=2)
    expected=evidence.get('renewal_policies_and_approvals',{})
    if charter_image!=evidence.get('charters') or items!=expected.get('items'):
        raise TransferUncertain('Charter, renewal policy, or approval changed after preparation')
    if evidence.get('snapshot_version',1)>=2 and monitor_images(row.project_id)!=evidence.get('operator_monitor'):
        raise TransferUncertain('Operator monitor policy or approval changed after preparation')
    blockers=_blockers(row.source,row.target)
    if any(blockers[k] for k in ['active_dispatch_ids','owner_intent_event_ids','unresolved_renewal_ids','active_latch_ids','active_job_ids']):
        raise TransferUncertain('Project acquired active or ambiguous work after preparation')


def _verify_document(row):
    return bool(row and row.digest == hashlib.sha256(json.dumps(
        {'summary':row.summary,'evidence':row.evidence},sort_keys=True,ensure_ascii=False).encode()).hexdigest())


def _authorized(row, approval, owner, *, purpose='execute', expected_new=None):
    from django.contrib.auth import get_user_model
    from django.db import connection
    users=get_user_model().objects.all()
    if connection.in_atomic_block:users=users.select_for_update()
    if row.owner_id!=owner.pk or not users.filter(pk=owner.pk,is_active=True,role='supervisor').exists():
        return False
    e=approval.evidence if approval and isinstance(approval.evidence,dict) else {}
    prep=row.preparation
    return bool(_verify_document(prep) and prep.evidence.get('status')=='authority_transfer_prepared_only'
        and prep.project_id==row.project_id and prep.session_key==row.source.key
        and prep.evidence.get('fingerprint')==row.key and prep.evidence.get('proposed_config_sha256')==row.new_config_sha256
        and _verify_document(approval) and approval.project_id==row.project_id
        and approval.session_key==row.source.key and e.get('status')=='authority_transfer_'+purpose+'_authorized'
        and (not prep.evidence.get('operator_monitor') or e.get('migrate_operator_monitor') is True)
        and e.get('journal_key')==row.key and e.get('fingerprint')==row.key
        and e.get('owner_id')==owner.pk and e.get('project_id')==row.project_id
        and e.get('source_id')==row.source_id and e.get('target_id')==row.target_id
        and e.get('old_config_sha256')==row.old_config_sha256
        and e.get('new_config_sha256')==(expected_new or row.new_config_sha256))


def _idle_proofs(journal, config):
    if load_config()!=config or pipe_identity(config['pipe'])!=config.get('owner_identity'):
        raise TransferUncertain('Owner pipe identity/configuration changed')
    proofs={}
    with OwnerTools(config) as client:
        for charter,thread in [(journal.source,journal.source.thread_id),(journal.target,journal.target.thread_id)]:
            context=str(uuid.uuid4())
            before=rollout_snapshot(thread)
            data=client.call('read_thread',{'threadId':thread,'hostId':'local','turnLimit':1,
                'includeOutputs':False,'maxOutputCharsPerItem':40},call_id=str(uuid.uuid4()),context_id=context)
            desktop=data.get('thread',{});turns=data.get('turns',[])
            rollout=rollout_snapshot(thread)
            latest=rollout.get('latest_turn')
            if (rollout['meta'].get('session_id',rollout['meta'].get('id'))!=thread or not rollout.get('completed')
                    or not before.get('completed') or before.get('latest_turn')!=latest
                    or before.get('sha256')!=rollout.get('sha256')
                    or desktop.get('id')!=thread or desktop.get('kind')!='codex' or desktop.get('hostId')!='local'
                    or desktop.get('status',{}).get('type') not in {'idle','notLoaded'}
                    or desktop.get('status',{}).get('activeFlags')
                    or not turns or turns[0].get('id')!=latest or turns[0].get('status')!='completed'
                    or Path(desktop.get('cwd','')).resolve()!=settings.BASE_DIR.parent.resolve()
                    or Path(rollout['meta'].get('cwd','')).resolve()!=settings.BASE_DIR.parent.resolve()):
                raise TransferUncertain('Rollout does not prove a closed current turn')
            proofs[str(charter.pk)]={'thread_id':thread,'desktop_idle':True,
                'rollout_sha256':rollout['sha256'],'latest_turn':rollout['latest_turn'],
                'sampled_at':timezone.now().isoformat()}
    if load_config()!=config:
        raise TransferUncertain('Channel configuration changed during idle reads')
    blockers=_blockers(journal.source,journal.target)
    if any(blockers[k] for k in ['active_dispatch_ids','owner_intent_event_ids','unresolved_renewal_ids','active_latch_ids','active_job_ids']):
        raise TransferUncertain('Project contains active or ambiguous work')
    return proofs


def _freeze(journal_id, owner, approval, *, rollback=False):
    config=load_config();new=dict(config)
    row_hint=AuthorityTransferJournal.objects.select_related('target').get(pk=journal_id)
    new['caller_thread_id']=row_hint.source.thread_id if rollback else row_hint.target.thread_id
    expected_new=_config_sha(new)
    with transaction.atomic():
        Project.objects.filter(pk=row_hint.project_id).update(name=F('name'))
        row=AuthorityTransferJournal.objects.select_for_update().select_related('source','target').get(pk=journal_id)
        if Project.objects.filter(pk=row.project_id).update(name=F('name'))!=1:
            raise ValidationError('Project disappeared before transfer freeze')
        if AuthorityTransferJournal.objects.filter(project_id=row.project_id,state__in=['frozen','config_written','ambiguous']).exclude(pk=row.pk).exists():
            raise ValidationError('Another transfer journal already freezes this project')
        if not rollback and row.new_config_sha256!=expected_new:
            raise PermissionDenied('Prepared target config fingerprint changed; prepare again')
        if (config.get('project_id')!=row.project_id or config.get('authorizing_user_id')!=owner.pk
                or (not rollback and config.get('caller_thread_id')!=row.source.thread_id)
                or row.target.project_id!=row.project_id or row.source.project_id!=row.project_id
                or row.target.key not in {'coordination','session_operator'}):
            raise PermissionDenied('Registered source, target, owner, or current caller changed')
        if row.owner_id!=owner.pk or not owner.is_active or owner.role!='supervisor': raise PermissionDenied()
        if not _authorized(row,approval,owner,purpose='rollback' if rollback else 'execute',expected_new=expected_new):
            raise PermissionDenied('Transfer requires a journal-bound explicit owner receipt')
        if not getattr(settings,'AUTHORITY_TRANSFER_EXECUTION_ENABLED',False):
            raise PermissionDenied('Authority transfer execution is disabled pending root review')
        allowed={'complete'} if rollback else {'prepared','idle_verified','authorized'}
        if row.state not in allowed: raise ValidationError('Journal requires recovery or is not executable')
        lease=uuid.uuid4().hex
        if rollback and expected_new!=row.old_config_sha256:
            raise PermissionDenied('Rollback authorization does not bind the original connector configuration')
        row.new_config_sha256=expected_new
        row.state='frozen';row.execution_enabled=True;row.authorization=approval
        row.rollback_authorization=approval if rollback else row.rollback_authorization
        row.lease_key=lease;row.error_code='';row.save()
        return row,lease


def _policy_plan(row, new_config):
    result=[]
    for key in ['backend','coordination','frontend','review','session_operator']:
        charter=SessionCharter.objects.filter(project_id=row.project_id,key=key).first()
        if not charter: continue
        policy=AutoRenewalPolicy.objects.filter(charter=charter).select_related('activation_receipt').first()
        if not policy: continue
        before={'charter_id':charter.pk,'policy_id':policy.pk,'enabled':policy.enabled,
            'owner_id':policy.owner_id,'receipt_id':policy.activation_receipt_id,
            'receipt_digest':policy.activation_receipt.digest if policy.activation_receipt_id else None}
        is_caller=charter.thread_id==new_config['caller_thread_id']
        if is_caller:
            receipt=document(charter.project,charter.key,f'authority-transfer:{row.key}:suspend:{policy.pk}',
                'تعليق تجديد الميثاق الذي أصبح قناة المالك نفسها.',
                {'status':'authority_transfer_caller_renewal_suspended','journal_key':row.key,
                 'policy_id':policy.pk,'reason':'dynamic caller self-renewal is forbidden',
                 'prior_enabled':policy.enabled,'prior_receipt_id':policy.activation_receipt_id})
            AutoRenewalPolicy.objects.filter(pk=policy.pk).update(enabled=False,activation_receipt=receipt,updated_at=timezone.now())
        elif policy.enabled:
            old=policy.activation_receipt
            if not old or not _verify_document(old): raise TransferUncertain('Existing renewal approval is not verifiable')
            evidence=dict(old.evidence);channel=evidence.get('owner_channel')
            if not isinstance(channel,dict) or set(channel)!={'caller_thread_id','server_sha256','owner_image_sha256'}:
                raise TransferUncertain('Existing owner approval does not have migratable scoped channel evidence')
            evidence['owner_channel']={'caller_thread_id':new_config['caller_thread_id'],
                'server_sha256':new_config['server_sha256'],'owner_image_sha256':new_config['owner_identity']['sha256']}
            evidence.update({'status':'automatic_renewal_activation_authorized','authority_transfer_journal':row.key,
                'owner_id':policy.owner_id,'policy_id':policy.pk,'charter_id':charter.pk})
            receipt=document(charter.project,charter.key,f'authority-transfer:{row.key}:activation:{policy.pk}',
                'إعادة إصدار موافقة التجديد المقيدة بالقناة الجديدة مع حفظ المالك والنطاق والافتراضي.',evidence)
            AutoRenewalPolicy.objects.filter(pk=policy.pk).update(activation_receipt=receipt,updated_at=timezone.now())
        result.append({'before':before,'after_enabled':False if is_caller else policy.enabled,
            'after_receipt_id':AutoRenewalPolicy.objects.get(pk=policy.pk).activation_receipt_id})
    return result


def _monitor_plan(row, new_config):
    from operations.models import OperatorMonitorPolicy
    result=[]
    if row.preparation.evidence.get('snapshot_version',1)<2:return result
    for before in monitor_images(row.project_id):
        policy=OperatorMonitorPolicy.objects.select_for_update().select_related('charter').get(pk=before['policy_id'])
        # Lock the optional approval separately; preserve the policy/charter locks.
        old=(ProjectDocumentation.objects.select_for_update().get(pk=policy.activation_receipt_id)
             if policy.activation_receipt_id else None)
        if policy.owner_id!=row.owner_id:
            raise PermissionDenied('Operator monitor belongs to another owner')
        if old:
            if not _verify_document(old):raise TransferUncertain('Operator monitor approval is not verifiable')
            evidence=dict(old.evidence)
            evidence.update({'owner_channel':{'caller_thread_id':new_config['caller_thread_id'],
                'server_sha256':new_config['server_sha256'],'owner_image_sha256':new_config['owner_identity']['sha256']},
                'authority_transfer_journal':row.key,'prior_activation_receipt_id':old.pk,
                'prior_activation_receipt_digest':old.digest})
            receipt=document(policy.project,policy.charter.key,f'authority-transfer:{row.key}:monitor:{policy.pk}',
                'إعادة إصدار اعتماد المشغّل للقناة الجديدة بإذن النقل الصريح مع حفظ النطاق والمالك والإعدادات.',evidence)
            OperatorMonitorPolicy.objects.filter(pk=policy.pk).update(activation_receipt=receipt,updated_at=timezone.now())
        elif policy.enabled:raise TransferUncertain('Enabled operator monitor has no explicit approval')
        result.append({'before':before,'after':monitor_images(row.project_id)[0]})
    return result


def _assert_monitor_after(row):
    if 'operator_monitor' not in row.after_snapshot:return
    if monitor_images(row.project_id)!=[item['after'] for item in row.after_snapshot['operator_monitor']]:
        raise TransferUncertain('Operator monitor policy changed after transfer; rollback CAS rejected')


def _restore_monitor(row):
    from operations.models import OperatorMonitorPolicy
    _assert_monitor_after(row)
    for item in row.after_snapshot.get('operator_monitor',[]):
        before=item['before']
        OperatorMonitorPolicy.objects.filter(pk=before['policy_id']).update(enabled=before['enabled'],
            charter_id=before['charter_id'],owner_id=before['owner_id'],activation_receipt_id=before['receipt_id'],
            scopes=before['scopes'],cooldown_seconds=before['cooldown_seconds'],updated_at=timezone.now())


def _commit_bindings(row,new_config,proofs,lease):
    with _configuration_lock():
        return _commit_bindings_locked(row,new_config,proofs,lease)


def _commit_bindings_locked(row,new_config,proofs,lease):
    with transaction.atomic():
        Project.objects.filter(pk=row.project_id).update(name=F('name'))
        current=AuthorityTransferJournal.objects.select_for_update().get(pk=row.pk)
        if current.state not in {'frozen','config_written'} or current.lease_key!=lease:
            raise TransferUncertain('Transfer journal lease changed')
        if not _authorized(current,current.authorization,current.owner):
            raise PermissionDenied('Transfer approval changed before database commit')
        if _config_sha(new_config)!=current.new_config_sha256:
            raise TransferUncertain('New connector configuration does not match journal CAS')
        if load_config()!=new_config: raise TransferUncertain('Connector configuration changed before DB commit')
        if current.source.thread_id!=current.before_snapshot['source_thread_id'] or current.target.thread_id!=current.before_snapshot['target_thread_id']:
            raise TransferUncertain('Charter binding changed before DB commit')
        _assert_preparation_current(current)
        policies=_policy_plan(current,new_config)
        monitor=_monitor_plan(current,new_config)
        from workspace.models import ModelJob
        current.idle_evidence=proofs;current.after_snapshot={'policies':policies,'caller_thread_id':new_config['caller_thread_id'],
            'work_marker':{'job_pk':ModelJob.objects.filter(charter__project_id=current.project_id).order_by('-pk').values_list('pk',flat=True).first() or 0,
                'dispatch_pk':SessionDispatch.objects.filter(target_charter__project_id=current.project_id).order_by('-pk').values_list('pk',flat=True).first() or 0}}
        if current.preparation.evidence.get('snapshot_version',1)>=2:current.after_snapshot['operator_monitor']=monitor
        current.state='complete';current.transferred_at=timezone.now();current.execution_enabled=False
        current.save(update_fields=['idle_evidence','after_snapshot','state','transferred_at','execution_enabled','updated_at'])
        return current


def execute_transfer(journal_id,owner,authorization_receipt_id):
    row=AuthorityTransferJournal.objects.select_related('source','target').get(pk=journal_id)
    approval=ProjectDocumentation.objects.get(pk=authorization_receipt_id)
    row,lease=_freeze(journal_id,owner,approval)
    try:
        config=load_config()
        if _config_sha(config)!=row.old_config_sha256: raise TransferUncertain('Old connector CAS mismatch')
        proofs=_idle_proofs(row,config)
        _assert_preparation_current(row)
        new=dict(config);new['caller_thread_id']=row.target.thread_id
        new_sha=_config_sha(new)
        with transaction.atomic():
            locked=AuthorityTransferJournal.objects.select_for_update().get(pk=row.pk)
            if locked.lease_key!=lease or locked.state!='frozen' or _config_sha(load_config())!=locked.old_config_sha256:
                raise TransferUncertain('Pre-write CAS failed')
            locked.idle_evidence=proofs;locked.new_config_sha256=new_sha
            locked.before_snapshot=locked.before_snapshot
            locked.save(update_fields=['idle_evidence','new_config_sha256','before_snapshot','updated_at'])
        save_config(new,expected=config)
        with transaction.atomic():
            AuthorityTransferJournal.objects.filter(pk=row.pk,lease_key=lease,state='frozen').update(state='config_written')
        return _commit_bindings(row,new,proofs,lease)
    except Exception as exc:
        AuthorityTransferJournal.objects.filter(pk=row.pk,lease_key=lease).update(state='ambiguous',execution_enabled=False,error_code=type(exc).__name__)
        raise


def recover_transfer(journal_id,owner):
    with _configuration_lock():
        return _recover_transfer_locked(journal_id,owner)


def _recover_transfer_locked(journal_id,owner):
    row=AuthorityTransferJournal.objects.select_related('source','target','authorization').get(pk=journal_id)
    if row.owner_id!=owner.pk or not owner.is_active or owner.role!='supervisor': raise PermissionDenied()
    if row.state not in {'frozen','config_written','ambiguous'} or not row.authorization_id:
        raise ValidationError('No recoverable authorized journal')
    if not getattr(settings,'AUTHORITY_TRANSFER_EXECUTION_ENABLED',False): raise PermissionDenied('Transfer execution disabled')
    if not _authorized(row,row.authorization,owner):
        raise PermissionDenied('Recovery requires the original journal-bound owner authorization')
    config=load_config();actual=_config_sha(config)
    rollback_pending=row.after_snapshot.get('rollback_pending')
    if row.state in {'frozen','ambiguous'} and rollback_pending and actual==row.new_config_sha256:
        AuthorityTransferJournal.objects.filter(pk=row.pk,lease_key=row.lease_key).update(
            state='complete',execution_enabled=False,rollback_authorization=None,error_code='')
        return AuthorityTransferJournal.objects.get(pk=row.pk)
    if row.state in {'frozen','ambiguous'} and rollback_pending and actual==rollback_pending.get('config_sha256'):
        if not _authorized(row,row.rollback_authorization,owner,purpose='rollback'):
            raise PermissionDenied('Rollback recovery requires its journal-bound owner receipt')
        with transaction.atomic():
            Project.objects.filter(pk=row.project_id).update(name=F('name'))
            current=AuthorityTransferJournal.objects.select_for_update().get(pk=row.pk)
            if not _authorized(current,current.rollback_authorization,owner,purpose='rollback'):
                raise PermissionDenied('Rollback authority was revoked before recovery commit')
            if (current.state not in {'frozen','ambiguous'} or current.lease_key!=row.lease_key
                    or current.lease_key!=rollback_pending.get('lease_key')
                    or _config_sha(load_config())!=rollback_pending.get('config_sha256')):
                raise TransferUncertain('Rollback lease changed')
            for item in current.after_snapshot.get('policies',[]):
                prior=item['before']
                live=AutoRenewalPolicy.objects.select_for_update().get(pk=prior['policy_id'])
                if live.enabled!=item['after_enabled'] or live.activation_receipt_id!=item['after_receipt_id']:
                    raise TransferUncertain('Renewal policy changed during interrupted rollback')
            _restore_monitor(current)
            for item in current.after_snapshot.get('policies',[]):
                prior=item['before'];AutoRenewalPolicy.objects.filter(pk=prior['policy_id']).update(
                    enabled=prior['enabled'],activation_receipt_id=prior['receipt_id'],updated_at=timezone.now())
            current.state='rolled_back';current.execution_enabled=False;current.save()
            return current
    if actual==row.new_config_sha256 and row.new_config_sha256:
        # File is already switched. Complete the same database phase; never repeat file write/RPC/create.
        AuthorityTransferJournal.objects.filter(pk=row.pk,state__in=['frozen','ambiguous']).update(state='config_written')
        return _commit_bindings_locked(row,config,row.idle_evidence,row.lease_key)
    if actual==row.old_config_sha256:
        # The external CAS never happened. Release the lease; a later explicit call
        # repeats all proofs and policy snapshots before attempting the one write.
        AuthorityTransferJournal.objects.filter(pk=row.pk,state__in=['frozen','config_written','ambiguous'],
            lease_key=row.lease_key).update(state='prepared',execution_enabled=False,lease_key='',
                authorization=None,error_code='recovered_before_config_write')
        return AuthorityTransferJournal.objects.get(pk=row.pk)
    AuthorityTransferJournal.objects.filter(pk=row.pk).update(state='ambiguous',execution_enabled=False,error_code='config_hash_unknown')
    raise TransferUncertain('Configuration matches neither before nor after image; quarantined')


def rollback_transfer(journal_id,owner,rollback_receipt_id):
    row=AuthorityTransferJournal.objects.select_related('source','target').get(pk=journal_id)
    approval=ProjectDocumentation.objects.get(pk=rollback_receipt_id)
    if row.state!='complete' or row.owner_id!=owner.pk or not owner.is_active or owner.role!='supervisor': raise PermissionDenied()
    if not _authorized(row,approval,owner,purpose='rollback'): raise PermissionDenied('Explicit journal-bound rollback approval required')
    if not getattr(settings,'AUTHORITY_TRANSFER_EXECUTION_ENABLED',False): raise PermissionDenied('Transfer execution disabled')
    config=load_config()
    if _config_sha(config)!=row.new_config_sha256: raise TransferUncertain('Post-transfer config changed; rollback CAS rejected')
    proofs=_idle_proofs(row,config)
    for charter_id, previous in row.idle_evidence.items():
        if proofs.get(charter_id,{}).get('latest_turn') != previous.get('latest_turn'):
            raise TransferUncertain('Desktop work occurred after transfer; rollback is unsafe')
    blockers=_blockers(row.source,row.target)
    if any(blockers[k] for k in ['active_dispatch_ids','owner_intent_event_ids','unresolved_renewal_ids','active_latch_ids','active_job_ids']):
        raise TransferUncertain('New work exists after transfer; rollback blocked')
    from workspace.models import ModelJob
    marker=row.after_snapshot.get('work_marker',{})
    if (ModelJob.objects.filter(charter__project_id=row.project_id,pk__gt=marker.get('job_pk',0)).exists()
            or SessionDispatch.objects.filter(target_charter__project_id=row.project_id,pk__gt=marker.get('dispatch_pk',0)).exists()):
        raise TransferUncertain('Work was created after transfer; rollback cannot safely return the caller')
    old=dict(config);old['caller_thread_id']=row.source.thread_id
    with transaction.atomic():
        Project.objects.filter(pk=row.project_id).update(name=F('name'))
        current=AuthorityTransferJournal.objects.select_for_update().get(pk=row.pk)
        if not _authorized(current,approval,owner,purpose='rollback'):
            raise PermissionDenied('Rollback authority was revoked before freeze')
        if current.state!='complete' or current.new_config_sha256!=_config_sha(config):
            raise TransferUncertain('Rollback journal CAS changed')
        _assert_monitor_after(current)
        for item in current.after_snapshot.get('policies',[]):
            live=AutoRenewalPolicy.objects.select_for_update().get(pk=item['before']['policy_id'])
            if live.enabled!=item['after_enabled'] or live.activation_receipt_id!=item['after_receipt_id']:
                raise TransferUncertain('Renewal policy changed after transfer; rollback CAS rejected')
        current.state='frozen';current.execution_enabled=True;current.lease_key=uuid.uuid4().hex
        current.rollback_authorization=approval
        current.after_snapshot={**current.after_snapshot,'rollback_pending':{'config_sha256':_config_sha(old),'lease_key':current.lease_key}}
        current.save(update_fields=['state','execution_enabled','lease_key','rollback_authorization','after_snapshot','updated_at'])
    # Freeze is now durable. Recheck both independent sources before the external CAS.
    proofs=_idle_proofs(row,config)
    for charter_id, previous in row.idle_evidence.items():
        if proofs.get(charter_id,{}).get('latest_turn') != previous.get('latest_turn'):
            raise TransferUncertain('Desktop work occurred during rollback freeze; rollback is unsafe')
    save_config(old,expected=config)
    with _configuration_lock(), transaction.atomic():
        Project.objects.filter(pk=row.project_id).update(name=F('name'))
        current=AuthorityTransferJournal.objects.select_for_update().get(pk=row.pk)
        if not _authorized(current,current.rollback_authorization,owner,purpose='rollback'):
            raise PermissionDenied('Rollback authority was revoked before commit')
        pending=current.after_snapshot.get('rollback_pending',{})
        if current.state!='frozen' or current.lease_key!=pending.get('lease_key') or _config_sha(load_config())!=pending.get('config_sha256'):
            raise TransferUncertain('Rollback config write needs recovery')
        _restore_monitor(current)
        for before in current.after_snapshot.get('policies',[]):
            prior=before['before'];live=AutoRenewalPolicy.objects.select_for_update().get(pk=prior['policy_id'])
            if live.enabled!=before['after_enabled'] or live.activation_receipt_id!=before['after_receipt_id']:
                current.state='ambiguous';current.execution_enabled=False;current.error_code='policy_cas_changed';current.save()
                raise TransferUncertain('Renewal policy changed during rollback')
            AutoRenewalPolicy.objects.filter(pk=prior['policy_id']).update(
                enabled=prior['enabled'],activation_receipt_id=prior['receipt_id'],updated_at=timezone.now())
        current.rollback_authorization=approval;current.idle_evidence={'rollback':proofs}
        current.state='rolled_back';current.execution_enabled=False;current.save()
    return current
