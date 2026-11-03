"""Runtime context threshold engine. Preparation only; no transport/session actions."""
import hashlib
import json
import logging
import re
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from workspace.models import SessionCharter, ProjectDocumentation
from workspace.memory import document
from .models import (ContextSource, ContextObservation, CapacityPolicy, CapacityState,
    CapacityMeasurement, CapacityEvent, CapacityHandover)
from .runtime_context import PARSER, SCHEMA, MAX_AGE, digest as text_digest

logger = logging.getLogger(__name__)
SOURCE_KIND = 'runtime_context'
SCHEMA_DIGEST = text_digest(json.dumps(SCHEMA))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def selected_policy(project):
    # A legacy policy can never become a fallback for runtime actions or display.
    return CapacityPolicy.objects.filter(decision__project=project, source_kind=SOURCE_KIND).order_by('-pk').first()


def policy_for(project):
    policy = selected_policy(project)
    if policy:
        return policy
    decision = ProjectDocumentation.objects.get(pk=269, project=project)
    from django.contrib.auth import get_user_model
    owner = get_user_model().objects.get(username='mesuef', role='supervisor')
    policy, _ = CapacityPolicy.objects.get_or_create(version=f'context-capacity-v3-runtime-disabled:{project.pk}', defaults={
        'decision':decision, 'decided_by':owner, 'decided_at':decision.created_at,
        'source_kind':SOURCE_KIND, 'thresholds_enabled':False, 'parser_version':PARSER,
        'source_schema_digest':SCHEMA_DIGEST,
        'source_semantics':'Codex post-sampling active_context_tokens / full_context_window_limit; step-boundary estimate; no continuous tokenizer claim'})
    return policy


def verified_document(record):
    return record.digest == digest({'summary':record.summary, 'evidence':record.evidence})


def thresholds_enabled(policy):
    if not policy or not policy.thresholds_enabled or policy.source_kind != SOURCE_KIND or not policy.validation_id:
        return False
    if policy.parser_version != PARSER or policy.source_schema_digest != SCHEMA_DIGEST:
        return False
    decision, validation = policy.decision, policy.validation
    evidence = validation.evidence
    if not isinstance(evidence, dict) or not isinstance(decision.evidence, dict):
        return False
    return (validation.project_id == decision.project_id and verified_document(decision) and verified_document(validation)
        and decision.evidence.get('production_activation') is True
        and evidence.get('status') == 'runtime_capacity_validation_verified'
        and evidence.get('source_kind') == SOURCE_KIND
        and evidence.get('semantics') == 'codex_active_context_step_estimate'
        and evidence.get('parser_version') == PARSER and evidence.get('schema_digest') == SCHEMA_DIGEST
        and evidence.get('original_logs_matched') is True
        and evidence.get('model_and_compaction_verified') is True
        and evidence.get('activation_authorized') is True)


_SOURCE_UNSET = object()


def inspect(charter, policy, now, state=None, *, source=_SOURCE_UNSET):
    """Read-only inspection. Only current runtime observations; no legacy samples."""
    if source is _SOURCE_UNSET:
        source = ContextSource.objects.filter(key='codex-runtime').first()
    observation = None
    def result(reason):
        return observation, source, reason
    if policy.source_kind != SOURCE_KIND:
        return result('source_kind_unapproved')
    if policy.decision.project_id != charter.project_id:
        return result('policy_project_mismatch')
    if not charter.thread_id:
        return result('missing_binding')
    if SessionCharter.objects.filter(thread_id=charter.thread_id).exclude(pk=charter.pk).exists():
        return result('conflicting_binding')
    if source is None:
        return result('source_missing')
    observations = ContextObservation.objects.filter(charter=charter, thread_id=charter.thread_id, generation=source.generation)
    observation = observations.order_by('-sampled_at', '-pk').first()
    if policy.parser_version != PARSER or policy.source_schema_digest != SCHEMA_DIGEST or source.schema_digest != SCHEMA_DIGEST:
        return result('schema_or_parser_unapproved')
    if source.status != 'available' or not re.fullmatch('[0-9a-f]{64}',source.identity):
        return result('source_unavailable')
    age_limit = min(policy.max_age_seconds, MAX_AGE)
    if source.checked_at > now:
        return result('source_future')
    if (now-source.checked_at).total_seconds() > age_limit:
        return result('source_stale')
    if observation is None:
        return result('current_generation_observation_missing')
    row = observation
    if row.parser_version != policy.parser_version:
        return result('observation_parser_unapproved')
    if (row.source_row <= 0 or row.sampled_at > now or row.imported_at > now
            or row.imported_at < row.sampled_at or row.sampled_at > source.checked_at
            or not re.fullmatch(r'pid:[0-9]+:[0-9a-f-]{36}',row.process_uuid)
            or not re.fullmatch('[0-9a-f]{64}',row.source_digest)):
        return result('invalid_provenance')
    expected = text_digest(':'.join([str(row.generation),row.process_uuid,str(row.source_row),row.thread_id,str(row.turn_id)]))
    if row.key != expected:
        return result('invalid_observation_key')
    if (now-row.sampled_at).total_seconds() > age_limit:
        return result('sample_stale')
    if not 0 < row.current <= 10**9 or not 0 < row.limit <= 10**9:
        return result('invalid_capacity')
    if not row.model:
        return result('model_unconfirmed')
    if observations.filter(sampled_at=row.sampled_at).exclude(current=row.current,limit=row.limit,
            model=row.model,turn_id=row.turn_id,process_uuid=row.process_uuid).exists():
        return result('conflicting_observations')
    if observations.filter(process_uuid=row.process_uuid,source_row__gt=row.source_row).exists():
        return result('source_order_conflict')
    if state is None:
        state = CapacityState.objects.filter(charter=charter,policy=policy,thread_id=charter.thread_id).select_related('last_observation').first()
    if state and state.last_observation_id and row.sampled_at < state.last_observation.sampled_at:
        return result('older_than_processed')
    if not thresholds_enabled(policy):
        return result('display_only_runtime')
    bindings = policy.validation.evidence.get('model_windows', [])
    if not isinstance(bindings,list) or not any(isinstance(b,dict) and b.get('model') == row.model
            and type(b.get('full_context_window')) is int and b['full_context_window'] == row.limit for b in bindings):
        return result('model_window_unvalidated')
    return result('')


def evidence_for(observation, source):
    return {'kind':SOURCE_KIND, 'observation_id':observation.pk if observation else None,
        'source':{'id':source.pk,'generation':str(source.generation),'identity':source.identity,
            'schema_digest':source.schema_digest,'checked_at':source.checked_at.isoformat()} if source else None,
        'observation':{'thread_id':observation.thread_id,'turn_id':str(observation.turn_id),
            'generation':str(observation.generation),'process_uuid':observation.process_uuid,
            'source_row':observation.source_row,'source_digest':observation.source_digest,
            'parser_version':observation.parser_version,'model':observation.model,
            'current':observation.current,'full_context_window':observation.limit,
            'sampled_at':observation.sampled_at.isoformat(),'imported_at':observation.imported_at.isoformat()} if observation else None}


@transaction.atomic
def evaluate(charter_id, *, policy, now=None):
    now = now or timezone.now()
    # Refresh persisted configuration: callers cannot activate an in-memory copy.
    policy = CapacityPolicy.objects.select_related('decision','validation').get(pk=policy.pk)
    charter = SessionCharter.objects.select_for_update().select_related('project').get(pk=charter_id)
    if policy.decision.project_id != charter.project_id:
        raise ValueError('Policy project mismatch')
    state, _ = CapacityState.objects.get_or_create(charter=charter,policy=policy,thread_id=charter.thread_id)
    # SQLite lock-upgrade contention aborts atomically; the next maintenance cycle retries.
    # PostgreSQL also serializes this state via the UPDATE/select_for_update locks.
    CapacityState.objects.filter(pk=state.pk).update(revision=F('revision')+1)
    state.refresh_from_db()
    observation, source, reason = inspect(charter,policy,now,state)
    evidence = evidence_for(observation,source)
    # Collector check timestamps do not make the same observation a new cycle.
    identity = {**evidence,'source':{k:v for k,v in evidence['source'].items() if k != 'checked_at'} if source else None}
    key = digest([state.pk,identity,reason])
    measurement, _ = CapacityMeasurement.objects.get_or_create(key=key, defaults={
        'state':state,'sample':None,'sync':None,'observation':observation,'context_source':source,
        'source_generation':source.generation if source else None,'source_checked_at':source.checked_at if source else None,
        'source_schema_digest':source.schema_digest if source else '', 'evidence':evidence,
        'trusted':not reason,'reason':reason,'current':observation.current if observation else None,
        'limit':observation.limit if observation else None,
        'percent':observation.current/observation.limit*100 if observation and not reason else None,'observed_at':now})
    if reason or state.last_observation_id == observation.pk:
        return measurement
    current, limit = observation.current*100, observation.limit
    if current < policy.warning_reset*limit:
        state.warning_armed = True
    if current < policy.prepare_reset*limit:
        state.prepare_armed = True
    for kind, threshold in [('warning',policy.warning_percent),('prepare',policy.prepare_percent)]:
        if current < threshold*limit or not getattr(state,kind+'_armed'):
            continue
        cycle = getattr(state,kind+'_cycle')+1
        event = CapacityEvent.objects.create(key=f'{state.pk}:{kind}:{cycle}',measurement=measurement,kind=kind)
        if kind == 'prepare':
            payload = {'project':{'id':charter.project_id,'name':charter.project.name},
                'charter':{'id':charter.pk,'key':charter.key,'name':charter.name,'thread_id':charter.thread_id},
                'measurement':{'id':measurement.pk,'observation_id':observation.pk,'source_kind':SOURCE_KIND,
                    'context':observation.current,'limit':observation.limit,'percent':measurement.percent,
                    'evidence':measurement.evidence},
                'policy':{'id':policy.pk,'version':policy.version,'decision_id':policy.decision_id,
                    'validation_id':policy.validation_id,'source_semantics':policy.source_semantics}}
            checksum = digest(payload)
            receipt = document(charter.project,charter.key,'capacity-handover:'+event.key,
                'تحضير محدود بدليل سياق runtime دون إرسال أو تغيير ربط المحادثة',{'payload':payload,'sha256':checksum})
            saved = ProjectDocumentation.objects.get(pk=receipt.pk)
            if not verified_document(saved) or saved.evidence != {'payload':payload,'sha256':checksum}:
                raise ValueError('Handover receipt verification failed')
            CapacityHandover.objects.create(event=event,observation=observation,payload=payload,digest=checksum,receipt=saved)
        setattr(state,kind+'_armed',False)
        setattr(state,kind+'_cycle',cycle)
    state.last_observation = observation
    state.save(update_fields=['warning_armed','prepare_armed','warning_cycle','prepare_cycle','last_observation'])
    return measurement


def maintenance():
    try:
        return _maintenance()
    except Exception as exc:
        code = type(exc).__name__
        logger.warning('Capacity maintenance failed error=%s',code)
        return {'evaluated':0,'errors':[{'operation':'capacity','error_type':code}]}


def _maintenance():
    result = {'evaluated':0,'errors':[]}
    for charter in SessionCharter.objects.select_related('project').order_by('pk'):
        try:
            evaluate(charter.pk,policy=policy_for(charter.project))
            result['evaluated'] += 1
        except Exception as exc:
            code = type(exc).__name__
            result['errors'].append({'operation':'capacity','charter_id':charter.pk,'error_type':code})
            logger.warning('Capacity evaluation failed charter=%s error=%s',charter.pk,code)
    return result


def snapshot(project, *, now=None):
    """Read-only; policy preparation and evaluation never happen during GET."""
    now = now or timezone.now()
    policy = selected_policy(project)
    enabled = thresholds_enabled(policy)
    rows = []
    source = ContextSource.objects.filter(key='codex-runtime').first() if policy else None
    for charter in SessionCharter.objects.filter(project=project).order_by('pk'):
        observation, source, reason = inspect(charter,policy,now,source=source) if policy else (None,None,'policy_missing')
        state = CapacityState.objects.filter(charter=charter,policy=policy,thread_id=charter.thread_id).first() if policy else None
        events = CapacityEvent.objects.filter(measurement__state=state,measurement__trusted=True,
            measurement__observation__generation=source.generation,measurement__observation__thread_id=charter.thread_id).order_by('-pk') if state and source and enabled and not reason else CapacityEvent.objects.none()
        latest = {}
        for kind in ['warning','prepare']:
            event = events.filter(kind=kind).first()
            latest[kind] = {'id':event.pk,'at':event.created_at.isoformat(),'observation_id':event.measurement.observation_id} if event else None
            if event and kind == 'prepare':
                handover = CapacityHandover.objects.filter(event=event).first()
                if handover:
                    latest[kind].update({'handover_id':handover.pk,'receipt_id':handover.receipt_id})
        rows.append({'charter_id':charter.pk,'key':charter.key,'name':charter.name,
            'trusted':not reason,'uncertain_reason':reason or None,'mode':'thresholds_enabled' if enabled else 'display_only',
            'measurement':{'sample_id':None,'sync_id':None,'observation_id':observation.pk,
                'current':observation.current,'limit':observation.limit,'percent':observation.current/observation.limit*100 if not reason else None,
                'display_percent':observation.current/observation.limit*100 if observation.limit else None,
                'source_kind':SOURCE_KIND,'sampled_at':observation.sampled_at.isoformat(),'imported_at':observation.imported_at.isoformat(),
                'source_id':observation.source_row,'evidence':evidence_for(observation,source)} if observation else None,
            'recent_warning':latest['warning'],'recent_preparation':latest['prepare']})
    from workspace.auto_renewal import AUTO_RENEWAL_SCOPES, execution_allowed
    from workspace.models import AutoRenewalPolicy
    auto_policies = AutoRenewalPolicy.objects.filter(charter__project=project,
        charter__key__in=AUTO_RENEWAL_SCOPES).select_related(
            'charter', 'owner', 'activation_receipt').order_by('charter__key')
    auto_sessions = [row.charter.name for row in auto_policies if execution_allowed(row)]
    return {'charter_count':len(rows),'policy_version':policy.version if policy else None,
        'automatic_renewal_enabled':bool(auto_sessions),
        'automatic_renewal_sessions':auto_sessions,
        'source_kind':SOURCE_KIND,'thresholds_enabled':enabled,
        'policy':{'id':policy.pk,'max_age_seconds':min(policy.max_age_seconds,MAX_AGE),
            'warning_percent':policy.warning_percent,'prepare_percent':policy.prepare_percent,
            'warning_reset':policy.warning_reset,'prepare_reset':policy.prepare_reset,
            'source_kind':policy.source_kind,'parser_version':policy.parser_version,'schema_digest':policy.source_schema_digest} if policy else None,
        'sessions':rows}
