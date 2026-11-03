"""Separate, request-scoped owner-tool capability. Never expands task-worker tools."""
import hashlib
import json
from pathlib import Path
import uuid
from django.db import transaction
from django.utils import timezone
from workspace.models import SessionRenewal, SessionCharter, SessionRenewalEvent
from workspace.memory import document
from .owner_tools import OwnerTools, OwnerToolsError, load_config
from .owner_channel import pipe_identity

TOOL_NAMES = ('create_thread', 'read_thread', 'list_projects')


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def trusted_config(config):
    from .authority_handover import assert_transfer_not_frozen
    from django.db import connection
    if connection.in_atomic_block:
        from workspace.models import Project
        from django.db.models import F
        Project.objects.filter(pk=config['project_id']).update(name=F('name'))
    try:assert_transfer_not_frozen(config['project_id'])
    except Exception as exc:raise OwnerToolsError('قناة المالك مجمدة لنقل صلاحية؛ لا تجديد') from exc
    if load_config() != config or pipe_identity(config['pipe']) != config.get('owner_identity'):
        raise OwnerToolsError('Renewal owner channel identity/configuration changed')


def schemas(tools):
    result = {name: tools.get(name, {}).get('inputSchema', {}) for name in TOOL_NAMES}
    create, read, projects = (result[name] for name in TOOL_NAMES)
    if (not {'prompt', 'target'} <= set(create.get('required', []))
            or not {'prompt', 'target'} <= set(create.get('properties', {}))
            or not {'threadId', 'hostId', 'turnLimit', 'includeOutputs', 'maxOutputCharsPerItem'}
                   <= set(read.get('properties', {}))
            or read.get('required') != ['threadId'] or projects.get('required', [])):
        raise OwnerToolsError('Required owner renewal tool schema unavailable')
    variants = create['properties']['target'].get('anyOf', [])
    project = [v for v in variants if v.get('properties', {}).get('type', {}).get('enum') == ['project']]
    if len(project) != 1 or not {'type', 'projectId', 'environment'} <= set(project[0].get('required', [])):
        raise OwnerToolsError('Local project creation contract unavailable')
    environments = project[0]['properties']['environment'].get('anyOf', [])
    if not any(v.get('properties', {}).get('type', {}).get('enum') == ['local'] for v in environments):
        raise OwnerToolsError('Local project environment unavailable')
    return {name: checksum(value) for name, value in result.items()}


def project_mapping(data, cwd):
    matches = [p for p in data.get('projects', []) if p.get('projectKind') == 'local'
        and p.get('hostId') == 'local' and isinstance(p.get('path'), str)
        and Path(p['path']).is_absolute() and Path(p['path']).resolve() == Path(cwd).resolve()]
    if len(matches) != 1 or not isinstance(matches[0].get('projectId'), str) or not matches[0]['projectId']:
        raise OwnerToolsError('Unique local Desktop project mapping required')
    return matches[0]['projectId']


def decode(result):
    try:
        if result.get('isError'):
            raise ValueError()
        parts = [p['text'] for p in result['content'] if p.get('type') == 'text']
        value = json.loads(parts[0]) if len(parts) == 1 else None
        if not isinstance(value, dict):raise ValueError()
        return value
    except (KeyError, ValueError, TypeError):
        raise OwnerToolsError('Invalid owner renewal receipt; never retry create') from None


class RenewalOwnerTools(OwnerTools):
    def __init__(self, config, capability, request_id, actor, approval_id=None):
        self.capability = capability
        self.request_id = request_id
        self.actor = actor
        self.approval_id = approval_id
        self.candidate = None
        self.mapped_project = None
        trusted_config(config)
        super().__init__(config=config)
        try:
            if schemas(self.tools) != capability['schema_sha256']:
                raise OwnerToolsError('Owner renewal schemas changed since preflight')
            self.authority()
        except Exception:
            self.close();raise

    def authority(self):
        from workspace.renewal import _actor, _approval
        row = SessionRenewal.objects.select_related('charter__policy', 'charter__project').get(pk=self.request_id)
        _actor(self.actor, row.charter, row.owner_id)
        if not row.approval_id:
            row.approval_id = self.approval_id
        _approval(row)
        trusted_config(self.config)
        channel = row.approval.evidence.get('owner_channel')
        expected_default = row.approval.evidence.get('expected_desktop_default', {})
        automatic = row.approval.evidence.get('automatic') is True
        if (row.approval.evidence.get('desktop_default_inheritance_authorized') is not True
                or (automatic and row.approval.evidence.get('default_profile_observed') is not True)
                or not isinstance(expected_default, dict) or not isinstance(expected_default.get('model'), str)
                or not expected_default['model'] or expected_default.get('thinking') not in
                   {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}):
            raise OwnerToolsError('Explicit Desktop-default inheritance scope required')
        expected = {'caller_thread_id': self.config['caller_thread_id'],
            'server_sha256': self.config['server_sha256'],
            'owner_image_sha256': self.config['owner_identity']['sha256']}
        if (channel != expected or self.capability.get('config_sha256') != checksum(self.config)
                or self.config['project_id'] != row.charter.project_id
                or self.config['authorizing_user_id'] != row.owner_id
                or self.config['caller_thread_id'] == row.source_thread_id):
            raise OwnerToolsError('Renewal requires independent, request-approved owner authority')
        if row.state in {'cancelled', 'rolled_back'}:
            raise OwnerToolsError('Renewal capability already consumed')
        caller = SessionCharter.objects.filter(project_id=row.charter.project_id,
            thread_id=self.config['caller_thread_id'], policy__enabled=True,
            state__in=['active', 'prepared', 'ready_waiting_for_task'])
        if caller.count() != 1:
            raise OwnerToolsError('Owner caller binding changed; authority handoff required')
        return row

    def call(self, name, arguments, *, call_id, context_id):
        if name not in TOOL_NAMES or name not in self.tools:
            raise OwnerToolsError('Tool outside separate renewal capability')
        row = self.authority()
        if name == 'read_thread':
            allowed = {row.source_thread_id, row.target_thread_id, self.candidate}
            if (set(arguments) != {'threadId', 'hostId', 'turnLimit', 'includeOutputs', 'maxOutputCharsPerItem'}
                    or arguments['threadId'] not in allowed or not arguments['threadId']
                    or arguments['hostId'] != 'local' or arguments['turnLimit'] != 1
                    or arguments['includeOutputs'] is not False or arguments['maxOutputCharsPerItem'] != 40):
                raise OwnerToolsError('Read outside renewal source/candidate scope')
        elif name == 'list_projects':
            if arguments != {}:raise OwnerToolsError('Unexpected project-list arguments')
        else:
            expected = {'prompt': row.bootstrap_prompt, 'title': row.charter.name + ' — تجديد',
                'target': {'type': 'project', 'projectId': self.mapped_project,
                           'environment': {'type': 'local'}}}
            if not self.mapped_project or arguments != expected:
                raise OwnerToolsError('Create must use mapped local project and exact default bootstrap')
            with transaction.atomic():
                from workspace.renewal import _lock, _unchanged, _quiet
                row = _lock(row.pk, self.actor)
                self.authority(); _unchanged(row); _quiet(row)
                if (row.state != 'creating' or not row.create_attempted or not row.claim_token
                        or row.events.filter(state='owner_create_intent').exists()):
                    raise OwnerToolsError('Create already attempted or not durably claimed')
                from workspace.models import AutoRenewalLatch
                latch = AutoRenewalLatch.objects.select_for_update().filter(renewal=row).first()
                if latch:
                    from workspace.auto_renewal import assert_auto_authority
                    assert_auto_authority(row, row.approval)
                receipt = document(row.charter.project, row.charter.key,
                    'renewal-owner-create:' + str(row.key), 'نية استدعاء إنشاء Desktop مرة واحدة',
                    {'request_key': str(row.key), 'call_id': call_id, 'context_id': context_id,
                     'caller_thread_id': self.config['caller_thread_id'], 'schema_sha256': self.capability['schema_sha256'],
                     'config_sha256': checksum(self.config), 'project_id': self.mapped_project,
                     'bootstrap_sha256': row.bootstrap_digest, 'claim_token': str(row.claim_token)})
                SessionRenewalEvent.objects.create(renewal=row, state='owner_create_intent', receipt=receipt)
                if latch:
                    latch.state = 'owner_intent'
                    latch.owner_intent_receipt = receipt
                    latch.save(update_fields=['state', 'owner_intent_receipt', 'updated_at'])
                    assert_auto_authority(row, row.approval)
        # Recheck the policy/owner receipt at the RPC boundary after intent is durable.
        self.authority()
        value = decode(self.rpc('tools/call', {'name': name, 'arguments': arguments, '_meta': {
            'codexThreadId': self.config['caller_thread_id'], 'codexTurnId': context_id, 'codexCallId': call_id}}))
        self.authority()  # Configuration/authority changes after create are ambiguous, never retried.
        if name == 'list_projects':
            self.mapped_project = project_mapping(value, row.context['cwd'])
            if self.mapped_project != self.capability.get('desktop_project_id'):
                raise OwnerToolsError('Desktop project mapping changed since preflight')
        return value
