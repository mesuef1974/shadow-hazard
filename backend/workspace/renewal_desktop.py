"""Default-profile Desktop adapter. Construction is local and never starts work."""
import uuid
from pathlib import Path
from django.utils import timezone
from .models import SessionRenewal, SessionCharter, ProjectDocumentation
from .memory import document
from execution_context.owner_tools import OwnerTools, OwnerToolsError, load_config
from execution_context.renewal_owner_tools import (RenewalOwnerTools, checksum,
    trusted_config, schemas, project_mapping, decode)
from execution_context.renewal_rollout import snapshot, bootstrap_evidence


def probe_desktop_support(charter, actor):
    """Metadata/read-only preflight, then a DB receipt. No create/start/turn."""
    from .renewal import _actor
    from token_usage.capacity import verified_document
    charter = SessionCharter.objects.select_related('policy', 'project').get(pk=charter.pk)
    actor = _actor(actor, charter)
    config = load_config(); trusted_config(config)
    if config['project_id'] != charter.project_id or config['authorizing_user_id'] != actor.pk:
        raise OwnerToolsError('Owner project/actor differs from renewal scope')
    if SessionCharter.objects.filter(project_id=charter.project_id,thread_id=config['caller_thread_id'],
            policy__enabled=True,state__in=['active','prepared','ready_waiting_for_task']).count()!=1:
        raise OwnerToolsError('Pinned owner caller is not uniquely registered in this project')
    with OwnerTools(config) as client:
        schema_hashes = schemas(client.tools)
        caller = client.call('read_thread', {'threadId': config['caller_thread_id'], 'hostId': 'local',
            'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 40},
            call_id=str(uuid.uuid4()), context_id=str(uuid.uuid4())).get('thread', {})
        if (caller.get('id') != config['caller_thread_id'] or caller.get('kind') != 'codex'
                or caller.get('hostId') != 'local' or not isinstance(caller.get('cwd'),str)
                or not Path(caller['cwd']).is_absolute() or Path(caller['cwd']).resolve() != Path(charter_root()).resolve()):
            raise OwnerToolsError('Pinned Desktop caller identity does not match project')
        projects = decode(client.rpc('tools/call', {'name': 'list_projects', 'arguments': {}, '_meta': {
            'codexThreadId': config['caller_thread_id'], 'codexTurnId': str(uuid.uuid4()),
            'codexCallId': str(uuid.uuid4())}}))
        project_id = project_mapping(projects, charter_root())
    trusted_config(config)
    supported = config['caller_thread_id'] != charter.thread_id
    evidence = {'status': 'desktop_renewal_capability_inspected', 'owner_id': actor.pk,
        'source_thread_id': charter.thread_id, 'caller_thread_id': config['caller_thread_id'],
        'config_sha256': checksum(config), 'schema_sha256': schema_hashes, 'desktop_project_id': project_id,
        'capability_supported': supported, 'operationally_verified': False,
        'reason': '' if supported else 'renewing_owner_caller_requires_separate_authority_handoff',
        'checked_at': timezone.now().isoformat(), 'default_profile_only': True,
        'owner_server_sha256': config['server_sha256'], 'owner_image_sha256': config['owner_identity']['sha256'],
        'create_start_turn_calls': 0, 'automatic_renewal': False}
    receipt = document(charter.project, charter.key, 'desktop-renewal-capability:' + str(uuid.uuid4()),
        'فحص أدوات وهوية مشروع Desktop فقط؛ تنفيذ الدورة ينتظر تجديدًا طبيعيًا مأذونًا', evidence)
    if not verified_document(ProjectDocumentation.objects.get(pk=receipt.pk)):
        raise OwnerToolsError('Capability receipt verification failed')
    return receipt


def charter_root():
    from django.conf import settings
    return str(settings.BASE_DIR.parent.resolve())


class DesktopRenewalAdapter:
    operationally_verified = False
    strict_owner_intent_boundary = True

    def __init__(self, request_id=None, actor=None, approval_id=None):
        self.supported = False
        self.reason = 'desktop_capability_preflight_required'
        self.request_id = request_id
        self.actor = actor
        self.approval_id = approval_id
        self.candidate = None
        self.config = None
        self.capability = None
        if request_id is None or actor is None:return
        from token_usage.capacity import verified_document
        row = self._row()
        receipt = ProjectDocumentation.objects.filter(project=row.charter.project,
            session_key=row.charter.key, task_key__startswith='desktop-renewal-capability:').order_by('-pk').first()
        if not receipt or not verified_document(receipt):return
        data = receipt.evidence
        if (data.get('status') != 'desktop_renewal_capability_inspected'
                or data.get('owner_id') != row.owner_id or data.get('source_thread_id') != row.source_thread_id
                or data.get('capability_supported') is not True):
            self.reason = data.get('reason') or 'capability_scope_mismatch'
            return
        try:
            config = load_config()
        except OwnerToolsError:
            self.reason = 'owner_configuration_unavailable';return
        if checksum(config) != data.get('config_sha256'):
            self.reason = 'owner_configuration_changed_since_preflight';return
        self.config = config
        self.capability = data
        self.supported = True
        self.reason = ''

    def _row(self):
        return SessionRenewal.objects.select_related('charter__project', 'charter__policy').get(pk=self.request_id)

    def _client(self):
        if not self.supported:raise OwnerToolsError(self.reason)
        client = RenewalOwnerTools(self.config, self.capability, self.request_id, self.actor, self.approval_id)
        client.candidate = self.candidate
        return client

    def bind_candidate(self, thread_id):
        # A timeout recovery may name a candidate only after independent local proof.
        row = self._row()
        if thread_id != row.target_thread_id:
            bootstrap_evidence(row, thread_id)
        self.candidate = thread_id

    def _inspect(self, client, row, thread_id):
        first = snapshot(thread_id)
        data = client.call('read_thread', {'threadId': thread_id, 'hostId': 'local', 'turnLimit': 1,
            'includeOutputs': False, 'maxOutputCharsPerItem': 40},
            call_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()))
        thread = data.get('thread', {});turns = data.get('turns', [])
        status = thread.get('status', {})
        second = snapshot(thread_id)
        if (first['sha256'] != second['sha256'] or thread.get('id') != thread_id
                or thread.get('kind') != 'codex' or thread.get('hostId') != 'local'
                or not isinstance(thread.get('cwd'),str) or not Path(thread['cwd']).is_absolute()
                or Path(thread['cwd']).resolve() != Path(row.context['cwd']).resolve()
                or status.get('type') not in {'idle', 'notLoaded'} or status.get('activeFlags')
                or not turns or turns[0].get('id') != second['latest_turn'] or turns[0].get('status') != 'completed'):
            raise OwnerToolsError('Fresh Desktop/rollout idle proof does not agree')
        return {'thread_id': thread_id, 'project_id': row.charter.project_id, 'cwd': row.context['cwd'],
            'observed_at': timezone.now().isoformat(), 'archived': False, 'state': status['type'],
            'last_turn_complete': True, 'owner_read_verified': True, 'activity_token': second['latest_turn']}

    def create(self, **kwargs):
        row = self._row()
        if kwargs != {'request_key': str(row.key), 'source_thread_id': row.source_thread_id,
                'prompt': row.bootstrap_prompt, 'project_id': row.charter.project_id,
                'cwd': row.context['cwd'], 'config_profile': 'default'}:
            raise OwnerToolsError('Creation parameters differ from authorized default renewal')
        with self._client() as client:
            first = self._inspect(client, row, row.source_thread_id)
            client.call('list_projects', {}, call_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()))
            second = self._inspect(client, row, row.source_thread_id)
            if first['activity_token'] != second['activity_token']:
                raise OwnerToolsError('Source activity changed before owner create intent')
            result = client.call('create_thread', {'prompt': row.bootstrap_prompt,
                'title': row.charter.name + ' — تجديد',
                'target': {'type': 'project', 'projectId': client.mapped_project, 'environment': {'type': 'local'}}},
                call_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()))
        # Queued worktree/client IDs are not actual thread IDs. Never retry create.
        if result.get('hostId', 'local') != 'local' or not result.get('threadId'):
            raise OwnerToolsError('No actual local Desktop thread identity returned; reconcile without retry')
        return {'thread_id': str(uuid.UUID(result['threadId']))}

    def inspect(self, thread_id):
        row = self._row()
        with self._client() as client:
            return self._inspect(client, row, thread_id)

    def delivery_receipt_id(self, request):
        row = self._row()
        if request.pk != row.pk:raise OwnerToolsError('Receipt request mismatch')
        with self._client() as client:
            client.authority()
            evidence = bootstrap_evidence(row, request.target_thread_id)
            self._inspect(client, row, request.target_thread_id)
            client.authority()
            key = f'renewal-bootstrap:{row.key}:{evidence["sha256"]}'
            previous = ProjectDocumentation.objects.filter(project=row.charter.project,
                session_key=row.charter.key, task_key=key).first()
            if previous:
                from token_usage.capacity import verified_document
                if not verified_document(previous):raise OwnerToolsError('Stored delivery receipt changed')
                evidence['observed_at'] = previous.evidence['source_evidence']['observed_at']
            receipt = document(row.charter.project, row.charter.key,
                key, 'تحقق مستقل من تسليم تهيئة Desktop الأصلية',
                {'status': 'desktop_bootstrap_delivery_verified', 'renewal_key': str(row.key),
                 'thread_id': request.target_thread_id, 'source_thread_id': row.source_thread_id,
                 'handover_id': row.handover_id, 'handover_digest': row.handover_digest,
                 'bootstrap_sha256': row.bootstrap_digest, 'context': row.context,
                 'config_profile': 'default', 'transport': 'owner-desktop-observation',
                 'source_evidence': evidence})
        return receipt.pk

    def assert_binding_authority(self):
        with self._client() as client:
            row=client.authority()
            # Re-read activity at the final boundary too, after delivery parsing.
            target=self._inspect(client,row,row.target_thread_id)
            source=self._inspect(client,row,row.source_thread_id)
            if row.activity!={'source':source['activity_token'],'target':target['activity_token']}:
                raise OwnerToolsError('Desktop activity changed at final binding boundary')
            client.authority()

    def record_completed_cycle(self, row):
        from token_usage.capacity import verified_document
        if row.pk!=self.request_id or row.state!='switched':raise OwnerToolsError('Cycle not switched')
        current=self._row()
        if current.charter.thread_id!=current.target_thread_id or not current.binding_receipt_id:
            raise OwnerToolsError('Natural cycle binding is not confirmed')
        delivery=ProjectDocumentation.objects.get(pk=current.delivery_receipt_id)
        if not verified_document(delivery) or not verified_document(current.binding_receipt):
            raise OwnerToolsError('Delivery or binding receipt changed')
        receipt=document(current.charter.project,current.charter.key,'desktop-renewal-natural-cycle:'+str(current.key),
            'اكتملت دورة تجديد Desktop يدوية بهذا الطلب؛ لا تفعيل للتجديد التلقائي',
            {'request_key':str(current.key),'state':'verified_for_this_manual_cycle',
             'capability_supported':True,'operationally_verified':True,'automatic_renewal':False,
             'source_thread_id':current.source_thread_id,'target_thread_id':current.target_thread_id,
             'binding_receipt':current.binding_receipt_id,'delivery_receipt':delivery.pk,
             'source_evidence':delivery.evidence['source_evidence'],'no_usage_write':True})
        if not verified_document(ProjectDocumentation.objects.get(pk=receipt.pk)):
            raise OwnerToolsError('Natural cycle receipt verification failed')
        return receipt.pk
