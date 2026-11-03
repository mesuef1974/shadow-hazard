"""Persistent, default-off consumer for verified runtime capacity handovers."""
import hashlib
import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone

from token_usage.capacity import (inspect as inspect_capacity, selected_policy,
    thresholds_enabled, verified_document)
from token_usage.models import CapacityHandover
from workspace.models import (AutoRenewalLatch, AutoRenewalPolicy, Decision,
    DocumentAccess, ProjectDocumentation, SessionCharter, Task)
from workspace.memory import document, instructions

AUTO_RENEWAL_SCOPES = {
    'coordination': 'coordination_default',
    'frontend': 'frontend_default',
    'review': 'review_default',
    'backend': 'backend_default',
}
MAX_HANDOVER_AGE = 120
MAX_DECISIONS = 3
MAX_TASKS = 5
MAX_REFERENCES = 8
MAX_PROJECT_DOCUMENTS = 3


class AutoRenewalError(ValueError):
    pass


class TransferBudgetError(AutoRenewalError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _owner_config():
    try:
        from execution_context.owner_tools import load_config
        return load_config()
    except Exception:
        return None


def _is_owner_caller(charter, config=None):
    config = config if config is not None else _owner_config()
    return bool(config and charter.thread_id and config.get('caller_thread_id') == charter.thread_id)


def policy_for(charter, *, create=True):
    if charter.key not in AUTO_RENEWAL_SCOPES:
        return None
    if not create:
        return AutoRenewalPolicy.objects.filter(charter=charter).select_related(
            'charter', 'owner', 'activation_receipt').first()
    policy, _ = AutoRenewalPolicy.objects.get_or_create(charter=charter, defaults={'enabled': False})
    return AutoRenewalPolicy.objects.select_related('owner', 'activation_receipt', 'charter').get(pk=policy.pk)


def activation_valid(policy):
    if not policy or not policy.enabled or not policy.owner_id or not policy.activation_receipt_id:
        return False
    owner = policy.owner
    receipt = policy.activation_receipt
    evidence = receipt.evidence
    expected_default = evidence.get('expected_desktop_default') if isinstance(evidence, dict) else None
    config = _owner_config()
    expected_channel = ({'caller_thread_id': config.get('caller_thread_id'),
        'server_sha256': config.get('server_sha256'),
        'owner_image_sha256': config.get('owner_identity', {}).get('sha256')}
        if config else None)
    return (owner.is_active and owner.role == 'supervisor'
        and policy.charter.key in AUTO_RENEWAL_SCOPES
        and not _is_owner_caller(policy.charter, config)
        and receipt.project_id == policy.charter.project_id
        and receipt.session_key == policy.charter.key and verified_document(receipt)
        and isinstance(evidence, dict)
        and evidence.get('status') == 'automatic_renewal_activation_authorized'
        and evidence.get('automatic') is True
        and evidence.get('scope') == AUTO_RENEWAL_SCOPES[policy.charter.key]
        and evidence.get('charter_id') == policy.charter_id
        and evidence.get('policy_id') == policy.pk
        and evidence.get('owner_id') == policy.owner_id
        and type(evidence.get('owner_id')) is int
        and evidence.get('allow_create') is True
        and evidence.get('allow_rebind') is True
        and evidence.get('desktop_default_inheritance_authorized') is True
        and evidence.get('default_profile_observed') is True
        and isinstance(expected_default, dict)
        and isinstance(expected_default.get('model'), str) and bool(expected_default['model'])
        and expected_default.get('thinking') in {'none','minimal','low','medium','high','xhigh','max','ultra'}
        and isinstance(evidence.get('owner_channel'), dict)
        and set(evidence['owner_channel']) == {'caller_thread_id','server_sha256','owner_image_sha256'}
        and evidence.get('owner_channel') == expected_channel
        and config.get('project_id') == policy.charter.project_id
        and config.get('authorizing_user_id') == policy.owner_id
        and evidence.get('transport') == 'owner-desktop-default')


def execution_allowed(policy):
    return bool(activation_valid(policy) and getattr(settings, 'AUTO_RENEWAL_EXECUTION_ENABLED', False))


@transaction.atomic
def activate_policy(policy_id, receipt_id):
    """Activate only from a verified, scoped owner receipt; never from a bare flag."""
    AutoRenewalPolicy.objects.filter(pk=policy_id).update(updated_at=F('updated_at'))
    policy = AutoRenewalPolicy.objects.select_for_update().select_related('charter').get(pk=policy_id)
    receipt = ProjectDocumentation.objects.get(pk=receipt_id)
    evidence = receipt.evidence
    if (policy.charter.key not in AUTO_RENEWAL_SCOPES or not verified_document(receipt)
            or receipt.project_id != policy.charter.project_id
            or receipt.session_key != policy.charter.key or not isinstance(evidence, dict)
            or evidence.get('status') != 'automatic_renewal_activation_authorized'
            or evidence.get('automatic') is not True
            or evidence.get('scope') != AUTO_RENEWAL_SCOPES[policy.charter.key]
            or evidence.get('charter_id') != policy.charter_id
            or evidence.get('policy_id') != policy.pk
            or evidence.get('allow_create') is not True or evidence.get('allow_rebind') is not True
            or evidence.get('desktop_default_inheritance_authorized') is not True
            or evidence.get('default_profile_observed') is not True
            or not isinstance(evidence.get('expected_desktop_default'), dict)
            or not isinstance(evidence['expected_desktop_default'].get('model'), str)
            or not evidence['expected_desktop_default'].get('model')
            or evidence['expected_desktop_default'].get('thinking') not in
                {'none','minimal','low','medium','high','xhigh','max','ultra'}
            or not isinstance(evidence.get('owner_channel'), dict)
            or set(evidence['owner_channel']) != {'caller_thread_id','server_sha256','owner_image_sha256'}
            or evidence.get('transport') != 'owner-desktop-default'):
        raise AutoRenewalError('Explicit coordination owner activation receipt required')
    owner = get_user_model().objects.filter(pk=evidence.get('owner_id'), is_active=True,
        role='supervisor').first()
    if not owner:
        raise AutoRenewalError('Activation receipt owner must be an active supervisor')
    from execution_context.owner_tools import load_config
    config = load_config()
    expected_channel = {'caller_thread_id': config['caller_thread_id'],
        'server_sha256': config['server_sha256'],
        'owner_image_sha256': config['owner_identity']['sha256']}
    if (evidence.get('owner_channel') != expected_channel
            or config['project_id'] != policy.charter.project_id
            or config['authorizing_user_id'] != owner.pk
            or _is_owner_caller(policy.charter, config)):
        raise AutoRenewalError('Owner channel in activation receipt does not match the configured owner')
    policy.owner = owner
    policy.activation_receipt = receipt
    policy.enabled = True
    policy.save(update_fields=['owner', 'activation_receipt', 'enabled', 'updated_at'])
    if not activation_valid(policy):
        raise AutoRenewalError('Stored activation receipt did not validate')
    return policy


def assert_auto_authority(renewal, approval=None):
    latch = AutoRenewalLatch.objects.select_related(
        'charter', 'handover', 'observation', 'renewal', 'charter__auto_renewal_policy').filter(
            renewal=renewal).first()
    if latch is None:
        raise AutoRenewalError('Automatic approval is not bound to a durable latch')
    policy = policy_for(latch.charter, create=False)
    if policy and connection.in_atomic_block:
        policy = AutoRenewalPolicy.objects.select_for_update().select_related(
            'owner', 'activation_receipt', 'charter__policy').get(pk=policy.pk)
    approval = approval or ProjectDocumentation.objects.filter(pk=renewal.approval_id).first()
    if (not execution_allowed(policy) or policy.owner_id != renewal.owner_id
            or latch.source_thread_id != latch.charter.thread_id
            or renewal.source_thread_id != latch.source_thread_id
            or latch.state in {'invalidated', 'stale', 'quarantined', 'ambiguous'}):
        raise AutoRenewalError('Automatic renewal policy, owner, or source binding is no longer valid')
    intent = renewal.events.filter(state='owner_create_intent').select_related('receipt').first()
    if intent and (not latch.owner_intent_receipt_id or latch.owner_intent_receipt_id != intent.receipt_id
            or not verified_document(intent.receipt)):
        raise AutoRenewalError('Durable automatic owner intent receipt is missing or changed')
    evidence = approval.evidence if approval else None
    expected = {'status': 'automatic_renewal_request_authorized', 'automatic': True,
        'allow_create': True, 'allow_rebind': True, 'allow_rollback': False,
        'transport': 'owner-desktop-default', 'request_key': str(latch.request_key),
        'renewal_key': str(renewal.key), 'latch_id': latch.pk, 'charter_id': latch.charter_id,
        'source_thread_id': latch.source_thread_id, 'owner_id': policy.owner_id,
        'policy_id': policy.pk, 'policy_updated_at': policy.updated_at.isoformat(),
        'activation_receipt_id': policy.activation_receipt_id,
        'desktop_default_inheritance_authorized': True,
        'default_profile_observed': True,
        'expected_desktop_default': policy.activation_receipt.evidence['expected_desktop_default'],
        'owner_channel': policy.activation_receipt.evidence['owner_channel'],
        'capacity_event_id': latch.handover.event_id,
        'observation_id': latch.observation_id}
    if (not approval or approval.project_id != latch.charter.project_id
            or approval.session_key != latch.charter.key or not verified_document(approval)
            or not isinstance(evidence, dict)
            or any(type(evidence.get(k)) is not bool for k in ['automatic','allow_create','allow_rebind','allow_rollback'])
            or any(evidence.get(k) != v for k, v in expected.items())):
        raise AutoRenewalError('Request-bound automatic owner receipt is invalid')
    return latch, policy


def _handover_is_fresh(handover, charter, capacity_policy, now):
    event = handover.event
    observation = handover.observation
    measurement = event.measurement
    source = measurement.context_source
    receipt = handover.receipt
    if (event.kind != 'prepare' or not measurement.trusted or measurement.reason
            or measurement.observation_id != observation.pk or observation.charter_id != charter.pk
            or observation.thread_id != charter.thread_id or not source
            or measurement.source_generation != observation.generation
            or measurement.source_generation != source.generation
            or source.status != 'available' or not source.identity
            or source.schema_digest != capacity_policy.source_schema_digest
            or measurement.source_schema_digest != source.schema_digest
            or now < observation.sampled_at or now < source.checked_at
            or (now - observation.sampled_at).total_seconds() > min(capacity_policy.max_age_seconds, MAX_HANDOVER_AGE)
            or (now - source.checked_at).total_seconds() > min(capacity_policy.max_age_seconds, MAX_HANDOVER_AGE)
            or measurement.percent is None or measurement.percent < capacity_policy.prepare_percent):
        return False
    payload = handover.payload
    expected_payload = {'project': {'id': charter.project_id, 'name': charter.project.name},
        'charter': {'id': charter.pk, 'key': charter.key, 'name': charter.name,
                    'thread_id': charter.thread_id}}
    if (not isinstance(payload, dict) or payload.get('charter') != expected_payload['charter']
            or payload.get('project') != expected_payload['project']
            or handover.digest != _digest(payload) or not verified_document(receipt)
            or receipt.evidence != {'payload': payload, 'sha256': handover.digest}
            or receipt.project_id != charter.project_id or receipt.session_key != charter.key):
        return False
    latest, source_now, reason = inspect_capacity(charter, capacity_policy, now)
    return bool(not reason and latest and source_now and source_now.generation == observation.generation)


def _invalidate_old_bindings(charter):
    old = AutoRenewalLatch.objects.filter(charter=charter).exclude(source_thread_id=charter.thread_id).exclude(
        state__in=['invalidated', 'switched'])
    old.update(state='invalidated', last_error='source_binding_changed', updated_at=timezone.now())


def _cycle_receipt(latch, renewal, policy_id, activation_receipt_id):
    receipt = document(latch.charter.project, latch.charter.key,
        f'auto-renewal-cycle:{latch.request_key}', 'اكتملت دورة تجديد تلقائي محكومة بموافقة المالك',
        {'automatic': True, 'request_key': str(latch.request_key),
         'renewal_key': str(renewal.key), 'policy_id': policy_id,
         'activation_receipt_id': activation_receipt_id,
         'source_thread_id': renewal.source_thread_id,
         'target_thread_id': renewal.target_thread_id,
         'binding_receipt_id': renewal.binding_receipt_id,
         'delivery_receipt_id': renewal.delivery_receipt_id,
         'no_usage_write': True})
    saved = ProjectDocumentation.objects.get(pk=receipt.pk)
    if not verified_document(saved):
        raise AutoRenewalError('Automatic cycle receipt failed verification')
    return saved


def _transfer_summary(payload):
    """Keep binding instructions in the evidence receipt; summarize recent work only."""
    charter = payload['charter']
    summary_data = {k: v for k, v in payload.items() if k not in {'kind', 'charter'}}
    summary_data['charter'] = {k: charter[k] for k in ('id', 'key', 'name', 'source_thread_id')}
    return json.dumps(summary_data, ensure_ascii=False, separators=(',', ':'))


@transaction.atomic
def _recover_committed_latch(latch):
    if not latch.renewal_id:
        return False
    renewal = latch.renewal
    if (renewal.state != 'switched' or renewal.charter.thread_id != renewal.target_thread_id
            or renewal.source_thread_id != latch.source_thread_id
            or not renewal.binding_receipt_id or not renewal.delivery_receipt_id):
        return False
    binding, delivery = renewal.binding_receipt, renewal.delivery_receipt
    approval = renewal.approval
    evidence = approval.evidence if approval and isinstance(approval.evidence, dict) else {}
    binding_evidence = binding.evidence if isinstance(binding.evidence, dict) else {}
    delivery_evidence = delivery.evidence if isinstance(delivery.evidence, dict) else {}
    delivered_source = delivery_evidence.get('source_evidence') if isinstance(delivery_evidence, dict) else None
    expected_default = evidence.get('expected_desktop_default') if isinstance(evidence, dict) else None
    policy = AutoRenewalPolicy.objects.filter(charter=latch.charter, pk=evidence.get('policy_id')).first()
    activation_receipt_id = evidence.get('activation_receipt_id')
    activation = ProjectDocumentation.objects.filter(pk=activation_receipt_id,
        project=latch.charter.project, session_key=latch.charter.key).first()
    if (not verified_document(binding) or not verified_document(delivery)
            or binding_evidence.get('automatic_renewal') is not True
            or binding_evidence.get('old_binding') != renewal.source_thread_id
            or binding_evidence.get('new_binding') != renewal.target_thread_id
            or binding_evidence.get('delivery_receipt_id') != delivery.pk
            or delivery_evidence.get('status') != 'desktop_bootstrap_delivery_verified'
            or delivery_evidence.get('renewal_key') != str(renewal.key)
            or delivery_evidence.get('thread_id') != renewal.target_thread_id
            or not approval or not verified_document(approval)
            or evidence.get('automatic') is not True
            or evidence.get('request_key') != str(latch.request_key)
            or evidence.get('latch_id') != latch.pk
            or not policy or evidence.get('policy_id') != policy.pk
            or not activation or not verified_document(activation)
            or not isinstance(activation.evidence, dict)
            or activation.evidence.get('status') != 'automatic_renewal_activation_authorized'
            or activation.evidence.get('automatic') is not True
            or activation.evidence.get('scope') != AUTO_RENEWAL_SCOPES.get(latch.charter.key)
            or activation.evidence.get('charter_id') != latch.charter_id
            or activation.evidence.get('policy_id') != policy.pk
            or activation.evidence.get('owner_id') != renewal.owner_id
            or evidence.get('owner_id') != renewal.owner_id
            or evidence.get('allow_create') is not True or evidence.get('allow_rebind') is not True
            or evidence.get('allow_rollback') is not False
            or evidence.get('owner_channel') != activation.evidence.get('owner_channel')
            or evidence.get('expected_desktop_default') != activation.evidence.get('expected_desktop_default')
            or activation.evidence.get('allow_create') is not True
            or activation.evidence.get('allow_rebind') is not True
            or activation.evidence.get('desktop_default_inheritance_authorized') is not True
            or activation.evidence.get('default_profile_observed') is not True):
        return False
    if (not isinstance(delivered_source, dict) or not isinstance(expected_default, dict)
            or delivered_source.get('model') != expected_default.get('model')
            or delivered_source.get('thinking') != expected_default.get('thinking')):
        return False
    if (binding.pk != renewal.binding_receipt_id or delivery.pk != renewal.delivery_receipt_id):
        return False
    _cycle_receipt(latch, renewal, policy.pk, activation.pk)
    latch.state = 'switched'
    latch.last_error = ''
    latch.save(update_fields=['state', 'last_error', 'updated_at'])
    return True


@transaction.atomic
def latch_handover(handover_id, *, now=None):
    now = now or timezone.now()
    handover = CapacityHandover.objects.select_related('event__measurement__context_source',
        'observation__charter__project', 'receipt').get(pk=handover_id)
    SessionCharter.objects.filter(pk=handover.observation.charter_id).update(state=F('state'))
    charter = SessionCharter.objects.select_for_update().select_related('project').get(pk=handover.observation.charter_id)
    if charter.key not in AUTO_RENEWAL_SCOPES or not charter.thread_id or _is_owner_caller(charter):
        return None
    policy = policy_for(charter)
    _invalidate_old_bindings(charter)
    capacity_policy = selected_policy(charter.project)
    if (not capacity_policy or not thresholds_enabled(capacity_policy)
            or not _handover_is_fresh(handover, charter, capacity_policy, now)):
        return None
    evidence = {'capacity_event_id': handover.event_id, 'handover_id': handover.pk,
        'observation_id': handover.observation_id, 'thread_id': charter.thread_id,
        'current_percent_at_trigger': handover.event.measurement.percent,
        'source_generation': str(handover.observation.generation),
        'sampled_at': handover.observation.sampled_at.isoformat(),
        'policy_id': capacity_policy.pk}
    try:
        row, created = AutoRenewalLatch.objects.get_or_create(
            charter=charter, source_thread_id=charter.thread_id,
            defaults={'handover': handover, 'observation': handover.observation,
                'request_key': uuid.uuid4(), 'state': 'latched_disabled',
                'evidence': evidence, 'evidence_digest': _digest(evidence)})
    except IntegrityError:
        row = AutoRenewalLatch.objects.get(charter=charter, source_thread_id=charter.thread_id)
        created = False
    if not created:
        if (row.state == 'preintent_backoff' and row.next_attempt_at
                and row.next_attempt_at > now):
            return row
        if row.state in {'latched_disabled', 'preintent_backoff'} and execution_allowed(policy):
            row.state = 'ready'
            row.save(update_fields=['state', 'updated_at'])
        return row
    if execution_allowed(policy):
        row.state = 'ready'
        row.save(update_fields=['state', 'updated_at'])
    return row


def bounded_transfer(latch):
    """Build a small, auditable bootstrap from live project records, never a model call."""
    charter = latch.charter
    transfer_key = f'auto-renewal-transfer:{latch.request_key}'
    handover_key = f'auto-renewal-handover:{latch.request_key}'
    existing_transfer = ProjectDocumentation.objects.filter(project=charter.project,
        session_key=charter.key, task_key=transfer_key).first()
    if existing_transfer:
        if (not verified_document(existing_transfer) or not isinstance(existing_transfer.evidence, dict)
                or existing_transfer.evidence.get('sha256') != _digest(existing_transfer.evidence.get('payload'))):
            raise AutoRenewalError('Persisted bounded transfer receipt is invalid')
        payload = existing_transfer.evidence['payload']
        if (payload.get('kind') != 'automatic_renewal_bounded_transfer'
                or payload.get('charter', {}).get('id') != charter.pk
                or payload.get('charter', {}).get('source_thread_id') != latch.source_thread_id
                or payload.get('capacity_handover_id') != latch.handover_id):
            raise AutoRenewalError('Persisted bounded transfer receipt is bound to different evidence')
        handover = ProjectDocumentation.objects.filter(project=charter.project,
            session_key=charter.key, task_key=handover_key).first()
        if handover:
            if (not verified_document(handover) or not isinstance(handover.evidence, dict)
                    or handover.evidence.get('source_transfer_receipt') != existing_transfer.pk
                    or handover.evidence.get('payload') != payload
                    or handover.evidence.get('sha256') != _digest(payload)):
                raise AutoRenewalError('Persisted bounded handover does not match its transfer receipt')
            return handover
        summary = _transfer_summary(payload)
        if len(summary) > 2000:
            raise TransferBudgetError('Bounded database transfer exceeds bootstrap summary limit')
        return document(charter.project, charter.key, handover_key, summary,
            {'source_transfer_receipt': existing_transfer.pk, 'payload': payload,
             'sha256': _digest(payload)})
    memory, memory_digest = instructions(charter.project)
    decisions = list(Decision.objects.filter(project=charter.project, private=False).order_by('-created_at')[:MAX_DECISIONS])
    tasks = list(Task.objects.filter(phase__project=charter.project, owner_only=False,
        state__in=['todo', 'doing', 'review'])
        .select_related('phase', 'assignee').order_by('-created_at')[:MAX_TASKS])
    references = list(DocumentAccess.objects.filter(team_visible=True).order_by('path')[:MAX_REFERENCES])
    project_documents = list(ProjectDocumentation.objects.filter(project=charter.project,
        session_key=charter.key).exclude(task_key__startswith='auto-renewal-transfer:').exclude(
        task_key__startswith='auto-renewal-handover:').order_by('-created_at', '-pk')[:MAX_PROJECT_DOCUMENTS])
    evidence = {'kind': 'automatic_renewal_bounded_transfer', 'charter': {
        'id': charter.pk, 'key': charter.key, 'name': charter.name, 'scope': charter.scope,
        'rules': charter.rules, 'source_thread_id': latch.source_thread_id},
        'memory_digest': memory_digest, 'capacity_handover_id': latch.handover_id,
        'decisions': [{'id': row.pk, 'title': row.title, 'body': row.body[:180]} for row in decisions],
        'tasks': [{'id': row.pk, 'title': row.title, 'state': row.state,
                   'phase': row.phase.title, 'description': row.description[:180]} for row in tasks],
        'references': [{'path': row.path} for row in references],
        'project_documents': [{'id': row.pk, 'task_key': row.task_key,
            'created_at': row.created_at.isoformat(), 'summary': row.summary[:180]}
            for row in project_documents]}
    summary = _transfer_summary(evidence)
    if len(summary) > 2000:
        raise TransferBudgetError('Bounded database transfer exceeds bootstrap summary limit')
    receipt = document(charter.project, charter.key,
        transfer_key, 'تسليم آلي محدود من سجلات المشروع وذاكرته',
        {'payload': evidence, 'sha256': _digest(evidence)})
    if not verified_document(ProjectDocumentation.objects.get(pk=receipt.pk)):
        raise AutoRenewalError('Bounded transfer receipt verification failed')
    handover = document(charter.project, charter.key, handover_key, summary,
        {'source_transfer_receipt': receipt.pk, 'payload': evidence, 'sha256': _digest(evidence)})
    return handover


def _request_approval(latch, policy, renewal):
    from execution_context.owner_tools import load_config
    config = load_config()
    evidence = {'status': 'automatic_renewal_request_authorized', 'automatic': True,
        'allow_create': True, 'allow_rebind': True, 'allow_rollback': False,
        'transport': 'owner-desktop-default', 'request_key': str(latch.request_key),
        'renewal_key': str(renewal.key), 'latch_id': latch.pk, 'charter_id': latch.charter_id,
        'source_thread_id': latch.source_thread_id, 'owner_id': policy.owner_id,
        'policy_id': policy.pk, 'policy_updated_at': policy.updated_at.isoformat(),
        'activation_receipt_id': policy.activation_receipt_id,
        'desktop_default_inheritance_authorized': True,
        'default_profile_observed': True,
        'expected_desktop_default': policy.activation_receipt.evidence['expected_desktop_default'],
        'owner_channel': {'caller_thread_id': config['caller_thread_id'],
            'server_sha256': config['server_sha256'],
            'owner_image_sha256': config['owner_identity']['sha256']},
        'capacity_event_id': latch.handover.event_id, 'observation_id': latch.observation_id}
    row = document(latch.charter.project, latch.charter.key,
        f'auto-renewal-approval:{latch.request_key}', 'تفويض آلي محدد لطلب تجديد واحد', evidence)
    if not verified_document(ProjectDocumentation.objects.get(pk=row.pk)):
        raise AutoRenewalError('Automatic request approval receipt verification failed')
    return row


def _fresh_current_observation(latch, now):
    capacity_policy = selected_policy(latch.charter.project)
    if not capacity_policy or not thresholds_enabled(capacity_policy):
        return False
    observation, source, reason = inspect_capacity(latch.charter, capacity_policy, now)
    # Pressure can fall after compaction; only trustworthy current evidence is required.
    return bool(not reason and observation and source and observation.thread_id == latch.source_thread_id
        and source.generation == observation.generation
        and (now - observation.sampled_at).total_seconds() <= min(capacity_policy.max_age_seconds, MAX_HANDOVER_AGE))


@transaction.atomic
def _claim_locked(latch_id, now):
    AutoRenewalLatch.objects.filter(pk=latch_id).update(updated_at=F('updated_at'))
    row = AutoRenewalLatch.objects.select_for_update().select_related('charter__project', 'handover__event__measurement',
        'handover__observation', 'observation').get(pk=latch_id)
    policy = policy_for(row.charter, create=False)
    if row.charter.thread_id != row.source_thread_id:
        row.state = 'invalidated'; row.last_error = 'source_binding_changed'
        row.save(update_fields=['state', 'last_error', 'updated_at'])
        return None
    expected_evidence = {'capacity_event_id': row.handover.event_id,
        'handover_id': row.handover_id, 'observation_id': row.observation_id,
        'thread_id': row.source_thread_id,
        'current_percent_at_trigger': row.handover.event.measurement.percent,
        'source_generation': str(row.observation.generation),
        'sampled_at': row.observation.sampled_at.isoformat(),
        'policy_id': selected_policy(row.charter.project).pk if selected_policy(row.charter.project) else None}
    if (row.evidence != expected_evidence or row.evidence_digest != _digest(row.evidence)
            or row.handover.observation_id != row.observation_id
            or row.handover.event.measurement.observation_id != row.observation_id):
        row.state = 'quarantined'; row.last_error = 'latch_evidence_mismatch'
        row.save(update_fields=['state', 'last_error', 'updated_at'])
        return None
    if not execution_allowed(policy):
        if row.state not in {'switched', 'invalidated'}:
            row.state = 'quarantined' if row.renewal_id else 'latched_disabled'
            row.last_error = 'activation_missing_or_revoked'
            row.save(update_fields=['state', 'last_error', 'updated_at'])
        return None
    if row.state in {'preintent_backoff','candidate_known','verified','owner_intent','ambiguous'} \
            and row.next_attempt_at and row.next_attempt_at > now:
        return None
    if row.state in {'latched_disabled', 'preintent_backoff'}:
        if row.state != 'candidate_known':
            row.state = 'ready'
    if row.state == 'claimed' and row.updated_at < now - timedelta(minutes=2):
        # A lost pre-intent claim can safely resume the same durable request key.
        if row.renewal_id:
            from workspace.models import SessionRenewal
            renewal = SessionRenewal.objects.get(pk=row.renewal_id)
            if renewal.events.filter(state='owner_create_intent').exists():
                if renewal.target_thread_id:
                    row.state = 'candidate_known'
                else:
                    row.state = 'ambiguous'; row.last_error = 'owner_intent_without_reconciled_result'
                    row.save(update_fields=['state', 'last_error', 'updated_at'])
                    return None
        row.state = 'ready'
    if row.state in {'owner_intent', 'ambiguous'} and row.renewal_id:
        from workspace.models import SessionRenewal
        renewal = SessionRenewal.objects.get(pk=row.renewal_id)
        if renewal.target_thread_id:
            row.state = 'candidate_known'
        else:
            row.state = 'quarantined'; row.last_error = 'unknown_candidate_after_owner_intent'
            row.save(update_fields=['state', 'last_error', 'updated_at'])
            return None
    if row.state not in {'ready', 'candidate_known', 'verified'}:
        return None
    if not _fresh_current_observation(row, now):
        row.state = 'stale'; row.last_error = 'current_context_not_fresh_and_trusted'
        row.save(update_fields=['state', 'last_error', 'updated_at'])
        return None
    row.claim_token = uuid.uuid4()
    row.state = 'claimed'
    row.save(update_fields=['claim_token', 'state', 'updated_at'])
    return row


def _claim(latch_id, now):
    try:
        return _claim_locked(latch_id, now)
    except OperationalError:
        # Database contention is a missed claim, never permission to proceed unlocked.
        return None


def _run_claimed(row, *, now=None, test_adapter=None):
    """Drive only an explicitly enabled, receipt-authorized request through the existing saga."""
    now = now or timezone.now()
    policy = policy_for(row.charter, create=False)
    if not execution_allowed(policy):
        raise AutoRenewalError('Automatic renewal activation was revoked')
    if not _fresh_current_observation(row, now):
        raise AutoRenewalError('Trusted current context evidence is required before execution')
    if row.charter.thread_id == getattr(settings, 'AUTO_RENEWAL_OWNER_CALLER_THREAD_ID', ''):
        raise AutoRenewalError('Owner caller requires a separate authority handoff')
    if test_adapter is None:
        from execution_context.owner_tools import load_config, OwnerToolsError
        try:
            config = load_config()
        except OwnerToolsError as exc:
            raise AutoRenewalError('Independent owner channel is unavailable') from exc
        if (config['caller_thread_id'] == row.source_thread_id
                or config['project_id'] != row.charter.project_id
                or config['authorizing_user_id'] != policy.owner_id):
            raise AutoRenewalError('Owner caller requires an independent authorized channel')
        activation_channel = policy.activation_receipt.evidence['owner_channel']
        actual_channel = {'caller_thread_id': config['caller_thread_id'],
            'server_sha256': config['server_sha256'],
            'owner_image_sha256': config['owner_identity']['sha256']}
        if actual_channel != activation_channel:
            raise AutoRenewalError('Owner channel differs from the activated channel')
    from workspace.renewal import (prepare_request, create_candidate, verify_candidate,
        commit_binding, RenewalError)
    from workspace.models import SessionRenewal
    renewal = SessionRenewal.objects.filter(pk=row.renewal_id).first() if row.renewal_id else None
    if not renewal:
        handover = bounded_transfer(row)
        renewal = prepare_request(row.charter_id, policy.owner, key=row.request_key,
            handover_id=handover.pk, automatic_latch_id=row.pk)
        approval = _request_approval(row, policy, renewal)
        renewal.approval_id = approval.pk
        renewal.save(update_fields=['approval'])
    else:
        approval = ProjectDocumentation.objects.filter(pk=renewal.approval_id).first()
        if approval is None:
            approval = _request_approval(row, policy, renewal)
            renewal.approval_id = approval.pk
            renewal.save(update_fields=['approval'])
    assert_auto_authority(renewal, approval)
    from workspace.renewal import _quiet
    try:
        _quiet(renewal)
    except Exception as exc:
        raise AutoRenewalError('Source lane is busy') from exc
    if test_adapter is not None and not getattr(settings, 'SESSION_RENEWAL_TEST_ADAPTERS', False):
        raise AutoRenewalError('Injected automatic adapters are test-only')
    if renewal and renewal.state in {'cancelled', 'creating'}:
        has_intent = renewal.events.filter(state='owner_create_intent').exists()
        if has_intent:
            if not renewal.target_thread_id:
                row.state = 'quarantined'; row.last_error = 'unknown_candidate_after_owner_intent'
                row.save(update_fields=['state', 'last_error', 'updated_at'])
                return row
        else:
            from workspace.renewal import recover_auto_preintent
            renewal = recover_auto_preintent(renewal.pk, policy.owner)
    # A candidate ID persisted before interruption resumes the same saga; no second create.
    if renewal.state in {'prepared', 'desktop_blocked'}:
        from workspace.renewal_desktop import probe_desktop_support
        if test_adapter is None:
            probe_desktop_support(row.charter, policy.owner)
        renewal = create_candidate(renewal.pk, policy.owner, approval_id=approval.pk, _test_adapter=test_adapter)
        if renewal.state == 'desktop_blocked':
            raise AutoRenewalError('Desktop owner capability is unavailable')
        if renewal.state == 'cancelled' and not renewal.events.filter(state='owner_create_intent').exists():
            raise AutoRenewalError('Automatic attempt stopped before owner create intent')
    if renewal.state == 'created' and renewal.target_thread_id:
        row.candidate_thread_id = renewal.target_thread_id
        row.state = 'candidate_known'
        row.save(update_fields=['candidate_thread_id', 'state', 'updated_at'])
    elif renewal.state in {'creation_uncertain', 'rollback_uncertain'}:
        if not renewal.target_thread_id:
            event = renewal.events.filter(state='creation_uncertain').select_related('receipt').order_by('-pk').first()
            candidate = event.receipt.evidence.get('candidate_thread_id') if event and isinstance(event.receipt.evidence, dict) else ''
            try:
                candidate = str(uuid.UUID(candidate))
            except (ValueError, TypeError, AttributeError):
                candidate = ''
            if not candidate or candidate == renewal.source_thread_id:
                row.state = 'quarantined'; row.last_error = 'unknown_candidate_after_owner_intent'
                row.save(update_fields=['state', 'last_error', 'updated_at'])
                return row
            renewal.target_thread_id = candidate
            renewal.save(update_fields=['target_thread_id'])
        row.candidate_thread_id = renewal.target_thread_id
        row.state = 'candidate_known'
        row.save(update_fields=['candidate_thread_id', 'state', 'updated_at'])
    if renewal.state in {'created', 'creation_uncertain', 'rollback_uncertain', 'verified'} and renewal.target_thread_id:
        assert_auto_authority(renewal, approval)
        renewal = verify_candidate(renewal.pk, policy.owner, _test_adapter=test_adapter)
    if renewal.state == 'verified':
        assert_auto_authority(renewal, approval)
        renewal = commit_binding(renewal.pk, policy.owner, _test_adapter=test_adapter)
    if renewal.state == 'switched':
        _cycle_receipt(row, renewal, policy.pk, policy.activation_receipt_id)
        row.state = 'switched'; row.last_error = ''
        row.save(update_fields=['state', 'last_error', 'updated_at'])
    return row


def reconcile(latch_id):
    """Read-only recovery view; never retries creation or commits a binding."""
    row = AutoRenewalLatch.objects.select_related('renewal', 'charter').get(pk=latch_id)
    if row.state == 'quarantined':
        return 'quarantined_read_only'
    if row.charter.thread_id != row.source_thread_id and row.state != 'switched':
        return 'source_binding_changed_requires_review'
    if row.renewal_id and row.renewal.state == 'switched':
        return 'committed_cycle_finalization_pending'
    if row.renewal_id and row.renewal.events.filter(state='owner_create_intent').exists():
        if row.renewal.target_thread_id:
            return 'known_candidate_requires_authorized_saga_resume'
        return 'quarantined_unknown_candidate'
    return row.state


def _maintain_charter(charter, result):
    policy = policy_for(charter)
    if not policy:
        return
    for row in AutoRenewalLatch.objects.filter(charter=charter).select_related('renewal__charter',
            'renewal__binding_receipt', 'renewal__delivery_receipt', 'renewal__approval'):
        try:
            _recover_committed_latch(row)
        except Exception as exc:
            result['errors'].append({'operation': 'recover_committed', 'session_key': charter.key,
                'latch_id': row.pk, 'error_type': type(exc).__name__})
    _invalidate_old_bindings(charter)
    for existing in AutoRenewalLatch.objects.filter(charter=charter).exclude(
            state__in=['switched','invalidated','quarantined','blocked']):
        if existing.renewal_id and not execution_allowed(policy):
            AutoRenewalLatch.objects.filter(pk=existing.pk).update(
                state='quarantined', last_error='activation_missing_or_revoked', updated_at=timezone.now())
        elif not execution_allowed(policy) and existing.state != 'latched_disabled':
            AutoRenewalLatch.objects.filter(pk=existing.pk).update(
                state='latched_disabled', last_error='activation_missing_or_revoked', updated_at=timezone.now())
        elif execution_allowed(policy) and existing.state == 'latched_disabled':
            if _fresh_current_observation(existing, timezone.now()):
                AutoRenewalLatch.objects.filter(pk=existing.pk).update(
                    state='ready', last_error='', updated_at=timezone.now())
            else:
                AutoRenewalLatch.objects.filter(pk=existing.pk).update(
                    state='stale', last_error='current_context_not_fresh_and_trusted', updated_at=timezone.now())
        elif execution_allowed(policy) and existing.state == 'stale' and not existing.renewal_id:
            if _fresh_current_observation(existing, timezone.now()):
                AutoRenewalLatch.objects.filter(pk=existing.pk).update(
                    state='ready', last_error='', updated_at=timezone.now())
        elif execution_allowed(policy) and existing.state == 'stale' and existing.renewal_id:
            has_intent = existing.renewal.events.filter(state='owner_create_intent').exists()
            if has_intent:
                AutoRenewalLatch.objects.filter(pk=existing.pk).update(
                    state='quarantined', last_error='stale_after_owner_intent', updated_at=timezone.now())
            elif _fresh_current_observation(existing, timezone.now()):
                AutoRenewalLatch.objects.filter(pk=existing.pk).update(
                    state='ready', last_error='', updated_at=timezone.now())
    capacity_policy = selected_policy(charter.project)
    if capacity_policy and thresholds_enabled(capacity_policy):
        handovers = CapacityHandover.objects.filter(observation__charter=charter).select_related(
            'event__measurement__context_source', 'observation', 'receipt').order_by('pk')
        for handover in handovers:
            try:
                row = latch_handover(handover.pk)
                if row:
                    result['latched'] += 1
                    if row.state == 'ready': result['ready'] += 1
            except Exception as exc:
                result['errors'].append({'operation': 'latch', 'session_key': charter.key,
                    'handover_id': handover.pk, 'error_type': type(exc).__name__})
    if not getattr(settings, 'AUTO_RENEWAL_EXECUTION_ENABLED', False):
        return
    row_ids = AutoRenewalLatch.objects.filter(charter=charter,
        state__in=['ready','candidate_known','verified','claimed','preintent_backoff','owner_intent','ambiguous'])
    for row_id in row_ids.values_list('pk', flat=True):
        try:
            claimed = _claim(row_id, timezone.now())
            if not claimed:
                continue
            _run_claimed(claimed)
            result['advanced'] += 1
        except Exception as exc:
            # Pre-intent failures back off; once owner intent exists, quarantine and never recreate.
            row = AutoRenewalLatch.objects.select_related('renewal').filter(pk=row_id).first()
            if row:
                renewal = row.renewal if row.renewal_id else None
                intent = bool(renewal and renewal.events.filter(state='owner_create_intent').exists())
                if not intent and row.renewal_id and renewal.state == 'prepared':
                    try:
                        from workspace.renewal import rollback
                        rollback(renewal.pk, renewal.owner)
                    except Exception:
                        pass
                known_candidate = bool(renewal and renewal.target_thread_id
                    and renewal.state in {'created','creation_uncertain','verified'})
                if intent and known_candidate and execution_allowed(policy_for(row.charter, create=False)):
                    row.state = 'verified' if renewal.state == 'verified' else 'candidate_known'
                else:
                    row.state = ('quarantined' if intent else
                        'blocked' if isinstance(exc, TransferBudgetError) else 'preintent_backoff')
                row.attempts = min(row.attempts + 1, 10)
                row.next_attempt_at = timezone.now() + timedelta(seconds=min(30 * (2 ** (row.attempts-1)), 1800))
                row.last_error = type(exc).__name__
                row.save(update_fields=['state','attempts','next_attempt_at','last_error','updated_at'])
            result['errors'].append({'operation': 'execute', 'session_key': charter.key,
                'latch_id': row_id, 'error_type': type(exc).__name__})


def maintenance():
    result = {'latched': 0, 'ready': 0, 'advanced': 0, 'errors': []}
    config = _owner_config()
    charters = SessionCharter.objects.select_related('project').filter(key__in=AUTO_RENEWAL_SCOPES).order_by('pk')
    if config:
        charters = charters.filter(project_id=config.get('project_id'))
    else:
        # Without an attested owner configuration, continue disabled latching only
        # for policies that were already deliberately provisioned.
        charters = charters.filter(auto_renewal_policy__isnull=False)
    for charter in charters:
        if _is_owner_caller(charter, config):
            continue
        try:
            _maintain_charter(charter, result)
        except Exception as exc:
            result['errors'].append({'operation': 'session_maintenance', 'session_key': charter.key,
                'error_type': type(exc).__name__})
    return result
