"""Audited manual renewal state machine; production Desktop execution is blocked.

No archive, task replay, model request, or automatic renewal is implemented here.
External creation has a durable no-retry boundary before the adapter is called.
"""
import hashlib
import re
import uuid
from pathlib import Path
from datetime import datetime
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from .bootstrap import prepare_bootstrap
from .memory import document, instructions
from .models import SessionCharter, SessionRenewal, SessionRenewalEvent, ProjectDocumentation, ModelJob
from .renewal_desktop import DesktopRenewalAdapter

GATING_STATES = ('creating', 'creation_uncertain', 'created', 'verified', 'rollback_uncertain')


class RenewalError(ValueError):
    pass


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _verified(record):
    from token_usage.capacity import verified_document
    return verified_document(record)


def _actor(actor, charter, owner_id=None):
    user = get_user_model().objects.get(pk=actor.pk)
    if not user.is_active or user.role != 'supervisor' or (owner_id and user.pk != owner_id):
        raise RenewalError('Supervisor authority required')
    if not charter.policy.enabled or charter.state not in {'active', 'prepared', 'ready_waiting_for_task'}:
        raise RenewalError('Charter policy disabled')
    return user


def _adapter(test_adapter, request_id, actor, approval_id=None):
    if test_adapter is not None:
        if not getattr(settings, 'SESSION_RENEWAL_TEST_ADAPTERS', False):
            raise RenewalError('Injected adapters are test-only; no production route')
        return test_adapter
    return DesktopRenewalAdapter(request_id=request_id, actor=actor, approval_id=approval_id)


def _context(charter):
    from execution_context.routing import instruction_files
    return {'project_id': charter.project_id, 'scope': charter.scope, 'rules': charter.rules,
            'policy_id': charter.policy_id, 'memory_digest': instructions(charter.project)[1],
            'model_policy': {'model': charter.policy.model, 'thinking': charter.policy.thinking,
                             'enabled': charter.policy.enabled},
            'instruction_sha256': {str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                   for path in instruction_files()},
            'cwd': str(settings.BASE_DIR.parent.resolve()), 'config_profile': 'default'}


def _lock(request_id, actor):
    # UPDATE reserves SQLite before reads; select_for_update alone is insufficient.
    SessionRenewal.objects.filter(pk=request_id).update(updated_at=F('updated_at'))
    row = SessionRenewal.objects.select_for_update().select_related('charter__policy', 'charter__project').get(pk=request_id)
    row.charter = SessionCharter.objects.select_for_update().select_related('policy', 'project').get(pk=row.charter_id)
    _actor(actor, row.charter, row.owner_id)
    return row


def _event(row, state, evidence):
    automatic = SessionRenewal.objects.filter(pk=row.pk, auto_latch__isnull=False).exists()
    receipt = document(row.charter.project, row.charter.key,
        f'renewal:{row.key}:{state}:{uuid.uuid4()}',
        ('حالة تجديد آلي محكومة: ' if automatic else 'حالة تجديد يدوي آمن: ') + state,
        {'renewal_key': str(row.key), 'source_thread_id': row.source_thread_id,
         'target_thread_id': row.target_thread_id,
         **({'automatic_renewal': True} if automatic else {}), **evidence})
    if not _verified(ProjectDocumentation.objects.get(pk=receipt.pk)):
        raise RenewalError('Renewal receipt verification failed')
    SessionRenewalEvent.objects.create(renewal=row, state=state, receipt=receipt)
    row.state = state
    row.save(update_fields=['state', 'updated_at'])
    return receipt


def _approval(row):
    approval = ProjectDocumentation.objects.filter(pk=row.approval_id).first()
    if approval and isinstance(approval.evidence, dict) and approval.evidence.get('automatic') is True:
        from workspace.auto_renewal import AutoRenewalError, assert_auto_authority
        try:
            assert_auto_authority(row, approval)
        except AutoRenewalError as exc:
            raise RenewalError(str(exc)) from exc
        return
    expected = {'renewal_key': str(row.key), 'charter_id': row.charter_id,
        'source_thread_id': row.source_thread_id, 'owner_id': row.owner_id,
        'allow_create': True, 'allow_rebind': True, 'allow_rollback': True,
        'automatic': False, 'transport': 'owner-desktop-default'}
    if (not approval or not _verified(approval) or approval.project_id != row.charter.project_id
            or approval.session_key != row.charter.key or not isinstance(approval.evidence, dict)
            or any(type(approval.evidence.get(k)) is not bool for k in ['allow_create', 'allow_rebind', 'allow_rollback', 'automatic'])
            or any(approval.evidence.get(k) != v for k, v in expected.items())):
        raise RenewalError('Explicit request-bound manual execution approval required')


@transaction.atomic
def recover_auto_preintent(request_id, actor):
    """Reopen only an automatic saga proven to have stopped before owner RPC intent."""
    row = _lock(request_id, actor)
    _approval(row)
    if (not hasattr(row, 'auto_latch') or row.events.filter(state='owner_create_intent').exists()
            or row.target_thread_id or row.state not in {'cancelled', 'creating'}):
        raise RenewalError('Automatic pre-intent recovery is not proven safe')
    row.create_attempted = False
    row.claim_token = None
    row.save(update_fields=['create_attempted', 'claim_token'])
    _event(row, 'prepared', {'automatic_preintent_recovery': True,
        'same_request_key': str(row.key), 'owner_create_called': False})
    return row


def _unchanged(row):
    if row.charter.thread_id != row.source_thread_id or _context(row.charter) != row.context:
        raise RenewalError('Source binding or context changed')
    if SessionCharter.objects.filter(thread_id=row.source_thread_id).exclude(pk=row.charter_id).exists():
        raise RenewalError('Source thread is bound to another charter')
    _stored_evidence(row)


def _stored_evidence(row):
    """Validate original evidence independently of subsequent memory changes."""
    if _hash(row.bootstrap_prompt) != row.bootstrap_digest:
        raise RenewalError('Source binding or context changed')
    for record_id, checksum in [(row.handover_id, row.handover_digest), (row.bootstrap_id, None)]:
        record = ProjectDocumentation.objects.get(pk=record_id)
        if (not _verified(record) or record.project_id != row.charter.project_id
                or record.session_key != row.charter.key or (checksum and record.digest != checksum)):
            raise RenewalError('Stored handover/bootstrap evidence changed')


def _quiet(row, *, after_switch=False):
    from execution_context.models import SessionDispatch
    charter = row.charter
    if SessionRenewal.objects.filter(charter=charter, state__in=GATING_STATES).exclude(pk=row.pk).exists():
        raise RenewalError('Another unresolved renewal owns the lane')
    jobs = ModelJob.objects.filter(Q(charter=charter) | Q(lane=charter.pk))
    dispatches = SessionDispatch.objects.filter(Q(target_charter=charter)
        | Q(target_thread_id__in=[row.source_thread_id, row.target_thread_id]))
    # Keep an acknowledged, unverified task quarantined on its exact retired
    # conversation, as job_lanes does. Never infer completion or replay it.
    retired = dispatches.filter(state='acknowledged', job__state='needs_review',
        target_charter=charter, job__charter=charter, job__lane=charter.pk,
        job__execution_thread_id=F('target_thread_id'), turn_key='', turns__isnull=True).exclude(
        target_thread_id__in=[row.source_thread_id, row.target_thread_id, '']).exclude(
        target_thread_id__in=SessionCharter.objects.values('thread_id'))
    retired_pairs = []
    for dispatch_id, job_id, thread_id in retired.values_list('pk', 'job_id', 'target_thread_id'):
        try:
            uuid.UUID(thread_id)
        except (ValueError, TypeError):
            continue
        retired_pairs.append((dispatch_id, job_id))
    jobs = jobs.exclude(pk__in=[job_id for _, job_id in retired_pairs])
    from workspace.renewal_history import finished_review_job_ids
    jobs = jobs.exclude(pk__in=finished_review_job_ids(charter))
    dispatches = dispatches.exclude(pk__in=[dispatch_id for dispatch_id, _ in retired_pairs])
    if jobs.exclude(state__in=['completed', 'cancelled', 'failed', 'blocked']).exists():
        raise RenewalError('Source lane has pending or unresolved work')
    if dispatches.exclude(state__in=['completed', 'finished_review', 'evidence_rejected']).exists():
        raise RenewalError('Desktop delivery lane is not quiescent')
    if after_switch:
        if (jobs.filter(created_at__gte=row.binding_receipt.created_at).exists()
                or dispatches.filter(created_at__gte=row.binding_receipt.created_at).exists()):
            raise RenewalError('New work exists after binding; rollback would conflict')


def _idle(adapter, row, thread_id):
    proof = adapter.inspect(thread_id)
    try:
        stamp = datetime.fromisoformat(proof['observed_at'])
        valid_time = 0 <= (timezone.now() - stamp).total_seconds() <= 30
    except (KeyError, TypeError, ValueError):
        valid_time = False
    if (not valid_time or proof.get('thread_id') != thread_id
            or proof.get('project_id') != row.charter.project_id
            or proof.get('cwd') != row.context['cwd'] or proof.get('archived') is not False
            or proof.get('state') not in {'idle', 'notLoaded'}
            or proof.get('last_turn_complete') is not True
            or proof.get('owner_read_verified') is not True or not proof.get('activity_token')):
        raise RenewalError('Fresh independent idle Desktop observation required')
    return proof['activity_token']


def _delivery(adapter, row):
    receipt_id = adapter.delivery_receipt_id(row)
    receipt = ProjectDocumentation.objects.get(pk=receipt_id)
    evidence = receipt.evidence
    expected = {'status': 'desktop_bootstrap_delivery_verified', 'renewal_key': str(row.key),
        'thread_id': row.target_thread_id, 'source_thread_id': row.source_thread_id,
        'handover_id': row.handover_id, 'handover_digest': row.handover_digest,
        'bootstrap_sha256': row.bootstrap_digest, 'context': row.context,
        'config_profile': 'default', 'transport': 'owner-desktop-observation'}
    if (not _verified(receipt) or receipt.project_id != row.charter.project_id
            or receipt.session_key != row.charter.key or not isinstance(evidence, dict)
            or any(evidence.get(k) != v for k, v in expected.items())):
        raise RenewalError('Independent delivery receipt does not match renewal')
    source = evidence.get('source_evidence')
    if (not isinstance(source, dict) or type(source.get('source_row')) is not int
            or source['source_row'] <= 0 or source.get('thread_id') != row.target_thread_id
            or not re.fullmatch('[0-9a-f]{64}', source.get('sha256', ''))):
        raise RenewalError('Original delivery source evidence required')
    try:
        uuid.UUID(source['turn_id'])
        observed = datetime.fromisoformat(source['observed_at'])
        if not 0 <= (receipt.created_at - observed).total_seconds() <= 120 or receipt.created_at < row.created_at:
            raise ValueError('Stale source attestation')
    except (KeyError, ValueError, TypeError):
        raise RenewalError('Original delivered turn identity and timely attestation required') from None
    return receipt


@transaction.atomic
def prepare_request(charter_id, actor, *, key, handover_id, automatic_latch_id=None):
    key = uuid.UUID(str(key))
    SessionCharter.objects.filter(pk=charter_id).update(state=F('state'))
    charter = SessionCharter.objects.select_related('policy', 'project').get(pk=charter_id)
    actor = _actor(actor, charter)
    uuid.UUID(charter.thread_id)
    if SessionCharter.objects.filter(thread_id=charter.thread_id).exclude(pk=charter.pk).exists():
        raise RenewalError('Source thread has conflicting bindings')
    existing = SessionRenewal.objects.filter(key=key).first()
    if existing:
        if (existing.charter_id, existing.owner_id, existing.handover_id) != (charter.pk, actor.pk, handover_id):
            raise RenewalError('Idempotency key belongs to another request')
        if automatic_latch_id:
            from workspace.models import AutoRenewalLatch
            latch = AutoRenewalLatch.objects.select_for_update().get(pk=automatic_latch_id)
            from workspace.auto_renewal import activation_valid, policy_for
            policy = policy_for(charter, create=False)
            if (latch.request_key != key or latch.source_thread_id != charter.thread_id
                    or not activation_valid(policy) or policy.owner_id != actor.pk):
                raise RenewalError('Automatic latch authorization changed')
            if latch.renewal_id and latch.renewal_id != existing.pk:
                raise RenewalError('Automatic latch already owns another saga')
            if not latch.renewal_id:
                latch.renewal = existing
                latch.save(update_fields=['renewal', 'updated_at'])
        return existing
    if SessionRenewal.objects.filter(charter=charter, source_thread_id=charter.thread_id).filter(
            Q(create_attempted=True) | ~Q(state='cancelled')).exists():
        raise RenewalError('Source already has a renewal request; reconcile it, never create another')
    handover = ProjectDocumentation.objects.get(pk=handover_id, project=charter.project, session_key=charter.key)
    bootstrap = prepare_bootstrap(charter, handover_id)
    prompt = f'[SHADOW_RENEWAL:{key}]\n' + bootstrap['prompt']
    row = SessionRenewal.objects.create(key=key, charter=charter, owner=actor,
        source_thread_id=charter.thread_id, handover=handover, handover_digest=handover.digest,
        bootstrap_id=bootstrap['receipt'], bootstrap_prompt=prompt,
        bootstrap_digest=_hash(prompt), context=_context(charter))
    evidence = {'automatic_renewal': False, 'config_profile': 'default'}
    if automatic_latch_id:
        from workspace.models import AutoRenewalLatch
        from workspace.auto_renewal import activation_valid, policy_for
        latch = AutoRenewalLatch.objects.select_for_update().get(pk=automatic_latch_id)
        policy = policy_for(charter, create=False)
        if (latch.request_key != key or latch.source_thread_id != charter.thread_id
                or not activation_valid(policy) or policy.owner_id != actor.pk):
            raise RenewalError('Automatic latch authorization changed')
        evidence = {'automatic_renewal': True, 'auto_latch_id': latch.pk,
            'policy_id': policy.pk, 'activation_receipt_id': policy.activation_receipt_id,
            'capacity_event_id': latch.handover.event_id, 'config_profile': 'default'}
        latch.renewal = row
        latch.save(update_fields=['renewal', 'updated_at'])
    _event(row, 'prepared', evidence)
    return row


def create_candidate(request_id, actor, *, approval_id=None, _test_adapter=None):
    adapter = _adapter(_test_adapter, request_id, actor, approval_id)
    with transaction.atomic():
        row = _lock(request_id, actor)
        if row.create_attempted or row.state not in {'prepared', 'desktop_blocked'}:
            return row
        if not adapter.supported:
            if row.state != 'desktop_blocked':
                _event(row, 'desktop_blocked', {'reason': adapter.reason, 'create_called': False})
            return row
        row.approval_id = approval_id
        _approval(row); _unchanged(row); _quiet(row)
        _idle(adapter, row, row.source_thread_id)
        row.create_attempted = True
        row.claim_token = uuid.uuid4()
        row.save(update_fields=['approval', 'create_attempted', 'claim_token'])
        _event(row, 'creating', {'claim_token': str(row.claim_token), 'no_retry_boundary': True})
    target = ''
    try:
        result = adapter.create(request_key=str(row.key), source_thread_id=row.source_thread_id,
            prompt=row.bootstrap_prompt, project_id=row.charter.project_id, cwd=row.context['cwd'],
            config_profile='default')
        target = str(uuid.UUID(result['thread_id']))
        if target == row.source_thread_id:
            raise RenewalError('Creation returned the original thread')
        with transaction.atomic():
            fresh = _lock(row.pk, actor)
            if fresh.state != 'creating' or fresh.claim_token != row.claim_token:
                raise RenewalError('Creation claim changed')
            fresh.target_thread_id = target
            fresh.save(update_fields=['target_thread_id'])
            receipt = _event(fresh, 'created', {'returned_thread_id': target,
                'claim_token': str(row.claim_token), 'delivery_not_yet_verified': True})
            fresh.creation_receipt = receipt
            fresh.save(update_fields=['creation_receipt'])
            return fresh
    except Exception as exc:
        # Includes failures saving the returned ID: never retry an external create.
        with transaction.atomic():
            fresh = _lock(row.pk, actor)
            if fresh.state != 'creating':
                return fresh
            if (getattr(adapter, 'strict_owner_intent_boundary', False)
                    and not fresh.events.filter(state='owner_create_intent').exists()):
                fresh.create_attempted = False
                fresh.claim_token = None
                fresh.save(update_fields=['create_attempted', 'claim_token'])
                _event(fresh, 'cancelled', {'error_type': type(exc).__name__,
                    'owner_create_called': False, 'reason': 'Rejected before durable owner RPC boundary'})
                return fresh
            _event(fresh, 'creation_uncertain', {'error_type': type(exc).__name__,
                'candidate_thread_id': target, 'retry_forbidden': True})
            return fresh


@transaction.atomic
def verify_candidate(request_id, actor, *, candidate_thread_id=None, _test_adapter=None):
    adapter = _adapter(_test_adapter, request_id, actor)
    row = _lock(request_id, actor)
    if not adapter.supported:
        raise RenewalError('Desktop verification adapter unavailable')
    if row.state not in {'created', 'creation_uncertain', 'rollback_uncertain', 'verified'} or not row.create_attempted:
        raise RenewalError('No created or ambiguous candidate to verify')
    _approval(row); _unchanged(row); _quiet(row)
    if candidate_thread_id:
        target = str(uuid.UUID(candidate_thread_id))
        if row.target_thread_id and row.target_thread_id != target:
            raise RenewalError('Candidate changed')
        row.target_thread_id = target
    if not row.target_thread_id or row.target_thread_id == row.source_thread_id:
        raise RenewalError('Distinct candidate identity required')
    if SessionCharter.objects.filter(thread_id=row.target_thread_id).exists():
        raise RenewalError('Candidate already bound')
    if hasattr(adapter, 'bind_candidate'):
        adapter.bind_candidate(row.target_thread_id)
    activity = {'source': _idle(adapter, row, row.source_thread_id),
                'target': _idle(adapter, row, row.target_thread_id)}
    receipt = _delivery(adapter, row)
    if activity['target'] != receipt.evidence['source_evidence']['turn_id']:
        raise RenewalError('Candidate has activity beyond the delivered bootstrap')
    row.delivery_receipt = receipt
    row.activity = activity
    row.save(update_fields=['target_thread_id', 'delivery_receipt', 'activity'])
    _event(row, 'verified', {'delivery_receipt_id': receipt.pk, 'no_binding_change': True})
    return row


@transaction.atomic
def commit_binding(request_id, actor, *, _test_adapter=None):
    adapter = _adapter(_test_adapter, request_id, actor)
    row = _lock(request_id, actor)
    if row.state == 'switched' and row.charter.thread_id == row.target_thread_id:
        return row
    if not adapter.supported or row.state != 'verified':
        raise RenewalError('Verified Desktop candidate required')
    _approval(row); _unchanged(row); _quiet(row)
    if SessionCharter.objects.filter(thread_id=row.target_thread_id).exists():
        raise RenewalError('Target is already bound')
    current = {'source': _idle(adapter, row, row.source_thread_id),
               'target': _idle(adapter, row, row.target_thread_id)}
    if current != row.activity or _delivery(adapter, row).pk != row.delivery_receipt_id:
        raise RenewalError('Activity or independent delivery evidence changed')
    _unchanged(row)
    if hasattr(adapter, 'assert_binding_authority'):
        adapter.assert_binding_authority()
    if SessionCharter.objects.filter(pk=row.charter_id, thread_id=row.source_thread_id).update(thread_id=row.target_thread_id) != 1:
        raise RenewalError('Source binding CAS failed')
    row.binding_receipt = _event(row, 'switched', {'delivery_receipt_id': row.delivery_receipt_id,
        'old_binding': row.source_thread_id, 'new_binding': row.target_thread_id, 'archived': False})
    row.save(update_fields=['binding_receipt'])
    from token_usage.models import TrackedSession
    tracked, _ = TrackedSession.objects.get_or_create(external_id=row.target_thread_id,
        defaults={'project': row.charter.project, 'name': row.charter.name})
    if tracked.project_id != row.charter.project_id or not tracked.enabled:
        raise RenewalError('Candidate tracking conflicts with project or is disabled')
    if hasattr(adapter, 'record_completed_cycle') and not hasattr(row, 'auto_latch'):
        adapter.record_completed_cycle(row)
    return row


@transaction.atomic
def rollback(request_id, actor, *, _test_adapter=None):
    adapter = _adapter(_test_adapter, request_id, actor)
    row = _lock(request_id, actor)
    if row.state in {'cancelled', 'rolled_back'}:
        return row
    if row.state in {'creating', 'creation_uncertain', 'rollback_uncertain'}:
        _event(row, 'rollback_uncertain', {'retry_create': False,
            'reason': 'External creation unresolved; reconcile candidate before releasing lane'})
        return row
    if row.state in {'prepared', 'desktop_blocked'}:
        _event(row, 'cancelled', {'create_attempted': False, 'binding_unchanged': True})
        return row
    if not adapter.supported:
        raise RenewalError('Independent Desktop rollback verification required')
    _approval(row)
    if _context(row.charter) != row.context:
        raise RenewalError('Context changed; rollback requires review')
    _quiet(row, after_switch=row.state == 'switched')
    current = {'source': _idle(adapter, row, row.source_thread_id),
               'target': _idle(adapter, row, row.target_thread_id)}
    if row.state == 'switched':
        if hasattr(adapter, 'assert_binding_authority'):
            adapter.assert_binding_authority()
        if current != row.activity:
            raise RenewalError('A new turn occurred; rollback would conflict')
        if SessionCharter.objects.filter(thread_id=row.source_thread_id).exclude(pk=row.charter_id).exists():
            raise RenewalError('Original thread bound elsewhere')
        if SessionCharter.objects.filter(pk=row.charter_id, thread_id=row.target_thread_id).update(thread_id=row.source_thread_id) != 1:
            raise RenewalError('Rollback binding CAS failed')
        state = 'rolled_back'
    elif row.state in {'created', 'verified'}:
        _unchanged(row)
        state = 'cancelled'
    else:
        raise RenewalError('Unsupported rollback transition')
    row.rollback_receipt = _event(row, state, {'orphan_thread_retained': row.target_thread_id,
        'archive_called': False, 'tasks_replayed': False})
    row.save(update_fields=['rollback_receipt'])
    return row
