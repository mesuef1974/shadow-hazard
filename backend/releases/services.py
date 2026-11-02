"""Server-side release contract. Mutations use CAS as well as database transactions."""
import hashlib
import json
import re
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, OperationalError
from django.db.models import F
from .models import Release, Evidence, Review, Approval, Deployment, EnvironmentLock, Event
from .models import MergeAttempt, RepositoryLock
from .git import GitMergeFailure
from django.conf import settings


class IdempotencyConflict(ValidationError):
    pass


def validate_source(task_id, job_id):
    from workspace.models import Task, ModelJob, SessionCharter
    if (task_id is None) == (job_id is None):
        raise ValidationError('Exactly one task or job is required')
    source_id = task_id if task_id is not None else job_id
    if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0:
        raise ValidationError('Source must be a positive integer')
    project_id = getattr(settings, 'RELEASE_PROJECT_ID', None)
    if project_id is None:
        project_id = SessionCharter.objects.filter(key='backend').values_list('project_id', flat=True).first()
    if project_id is None:
        raise ValidationError('Release project is not configured')
    belongs = (Task.objects.filter(pk=task_id, phase__project_id=project_id).exists() if task_id is not None
        else ModelJob.objects.filter(pk=job_id, charter__project_id=project_id).exists())
    if not belongs:
        raise ValidationError('Source is outside the release project or does not exist')


def merge_release(release_id, *, actor, key, adapter=None):
    supervisor(actor)
    with transaction.atomic():
        release = Release.objects.select_for_update().get(pk=release_id)
        old = MergeAttempt.objects.filter(key=key).first()
        if old:
            event = Event.objects.get(key=key)
            if old.release_id != release_id or event.actor_id != actor.pk:
                raise ValidationError('Idempotency conflict')
            return old
        if adapter is None:
            from .factories import git_factory
            adapter = git_factory(release)
        if release.fingerprint != release_fingerprint(release.commit, release.environment, release.manifest,
                release.repository, release.source_ref, release.target_branch, release.target_commit):
            raise ValidationError('Pinned fingerprint mismatch')
        act(release_id, actor=actor, action='merge', key=key, _reserve_merge=True)
        lock, _ = RepositoryLock.objects.get_or_create(repository=release.repository)
        attempt = MergeAttempt.objects.create(release=release, key=key)
        if not RepositoryLock.objects.filter(pk=lock.pk, attempt__isnull=True).update(attempt=attempt):
            raise ValidationError('Repository busy or unresolved')
    try:
        result = adapter.merge(release)
        if result != release.commit:
            raise RuntimeError('Unexpected result commit')
        state = 'merged'
    except GitMergeFailure:
        state, result = 'merge_failed', ''
    except Exception:
        state, result = 'unknown', ''
    with transaction.atomic():
        MergeAttempt.objects.filter(pk=attempt.pk).update(state=state, result_commit=result)
        Release.objects.filter(pk=release_id).update(state=state)
        if state != 'unknown':
            RepositoryLock.objects.filter(attempt=attempt).update(attempt=None)
    attempt.refresh_from_db()
    return attempt


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def supervisor(actor):
    if not actor.is_active or actor.role != 'supervisor':
        raise PermissionDenied('Supervisor required')


def fingerprint(commit, environment, manifest):
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValidationError('An immutable full git SHA is required')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', environment):
        raise ValidationError('Invalid environment')
    # Manifest contains references/digests, never configuration values or secrets.
    if set(manifest) != {'artifact_sha256', 'migration_plan_sha256', 'settings_sha256'} or any(
        not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for v in manifest.values()
    ):
        raise ValidationError('Three SHA-256 manifest digests required')
    return digest([commit, environment, manifest])


def release_fingerprint(commit, environment, manifest, repository, source_ref, target_branch, target_commit):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', repository) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', target_commit):
        raise ValidationError('Repository alias and full target SHA required')
    for ref in (source_ref, target_branch):
        if not re.fullmatch(r'refs/heads/[a-zA-Z0-9_/-]+', ref) or '..' in ref or ref.endswith('/'):
            raise ValidationError('Full branch reference required')
    return digest([fingerprint(commit, environment, manifest), repository, source_ref, target_branch, target_commit])


def create_release(*, actor, commit, environment, manifest, repository, source_ref, target_branch, target_commit, task_id=None, job_id=None):
    if not actor.is_active:
        raise PermissionDenied('Active author required')
    validate_source(task_id, job_id)
    return Release.objects.create(author=actor, task_id=task_id, job_id=job_id,
        commit=commit, environment=environment, manifest=manifest,
        repository=repository, source_ref=source_ref, target_branch=target_branch, target_commit=target_commit,
        fingerprint=release_fingerprint(commit, environment, manifest, repository, source_ref, target_branch, target_commit))


def create_release_once(*, actor, key, data):
    """Global durable key across clients; uniqueness arbitrates concurrent inserts.

    Store the original input digest so later release edits cannot change replay
    semantics. Creation and the key are committed in the same database insert.
    """
    # A bounded retry handles SQLite write contention. All effects are one atomic
    # insert guarded by the same unique key; no external operation is repeated.
    for attempt in range(2):
        try:
            return _create_release_once(actor=actor, key=key, data=data)
        except OperationalError as exc:
            if attempt or 'locked' not in str(exc).lower():
                raise
            import time
            time.sleep(0.02)


def _create_release_once(*, actor, key, data):
    if not actor.is_active:
        raise PermissionDenied('Active author required')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', key):
        raise ValidationError('Idempotency-Key header is required (1-100 ASCII characters)')
    fields = {'commit','environment','manifest','repository','source_ref','target_branch','target_commit','task_id','job_id'}
    required = fields - {'task_id','job_id'}
    if not isinstance(data, dict) or not required.issubset(data) or set(data) - fields:
        raise ValidationError('Invalid release payload')
    values = {**data, 'task_id':data.get('task_id'), 'job_id':data.get('job_id')}
    payload_digest = digest([actor.pk, values])
    old = Release.objects.filter(creation_key=key).first()
    if old:
        if old.creation_digest != payload_digest:
            raise IdempotencyConflict('Idempotency key reused with different input')
        return old, False
    validate_source(values['task_id'], values['job_id'])
    fp = release_fingerprint(*(values[name] for name in ['commit','environment','manifest','repository','source_ref','target_branch','target_commit']))
    row, created = Release.objects.get_or_create(creation_key=key, defaults={
        **values, 'author':actor, 'fingerprint':fp, 'creation_digest':payload_digest})
    if row.creation_digest != payload_digest:
        raise IdempotencyConflict('Idempotency key reused with different input')
    return row, created


@transaction.atomic
def act(release_id, *, actor, action, key, data=None, _reserve_merge=False):
    data = data or {}
    if not actor.is_active or not key or len(key) > 100:
        raise PermissionDenied('Active actor and idempotency key required')
    payload = digest([release_id, actor.pk, action, data])
    previous = Event.objects.filter(key=key).first()
    if previous:
        if previous.payload_digest != payload:
            raise ValidationError('Idempotency key reused with different input')
        return previous
    release = Release.objects.select_for_update().get(pk=release_id)
    # Atomic compare-and-swap also protects databases without row locks.
    if not Release.objects.filter(pk=release.pk, revision=release.revision).update(revision=F('revision') + 1):
        raise ValidationError('Concurrent modification; reload')
    if release.state in {'deploying', 'merging', 'unknown'}:
        raise ValidationError('Operation unresolved; manual reconciliation required')
    if action == 'revise':
        if actor.pk != release.author_id or release.state != 'draft':
            raise PermissionDenied('Only draft author can revise; create a new release after pinning')
        release.commit = data['commit']
        release.environment = data['environment']
        release.manifest = data['manifest']
        release.fingerprint = release_fingerprint(release.commit, release.environment, release.manifest, release.repository, release.source_ref, release.target_branch, release.target_commit)
        Approval.objects.filter(release=release).update(withdrawn=True)
    elif action == 'pin':
        if actor.pk != release.author_id or release.state != 'draft':
            raise PermissionDenied('Only draft author can pin')
        release.state = 'pinned'
    elif action == 'evidence':
        supervisor(actor)
        if release.state != 'pinned' or data['kind'] not in {'tests', 'migration', 'settings'}:
            raise ValidationError('Pinned release and known evidence kind required')
        if not data.get('reference') or len(data['reference']) > 500:
            raise ValidationError('Evidence reference required')
        Evidence.objects.create(release=release, fingerprint=release.fingerprint, actor=actor,
            kind=data['kind'], reference=data['reference'], accepted=data.get('accepted') is True)
    elif action == 'review':
        if actor.pk == release.author_id or release.state != 'pinned':
            raise PermissionDenied('Independent reviewer required')
        Review.objects.create(release=release, fingerprint=release.fingerprint,
            reviewer=actor, accepted=data.get('accepted') is True)
    elif action == 'merge':
        if not _reserve_merge:
            raise ValidationError('Use merge endpoint/service')
        supervisor(actor)
        latest_evidence = {}
        for evidence in release.evidence.filter(fingerprint=release.fingerprint).order_by('pk'):
            latest_evidence[evidence.kind] = evidence.accepted
        kinds = {kind for kind, accepted in latest_evidence.items() if accepted}
        latest = Review.objects.filter(release=release, fingerprint=release.fingerprint).order_by('-pk').first()
        if release.state != 'pinned' or kinds != {'tests', 'migration', 'settings'} or not latest or not latest.accepted or latest.reviewer_id == release.author_id:
            raise ValidationError('Accepted evidence and independent review required')
        release.state = 'merging'
    elif action == 'approve':
        supervisor(actor)
        if release.state not in {'merged', 'healthy', 'failed'} or data.get('fingerprint') != release.fingerprint or data.get('environment') != release.environment:
            raise ValidationError('Approval must explicitly match release and environment')
        purpose = data.get('purpose', 'deploy')
        if purpose not in {'deploy', 'rollback'}:
            raise ValidationError('Invalid purpose')
        Approval.objects.create(release=release, fingerprint=release.fingerprint, environment=release.environment, actor=actor, purpose=purpose)
    elif action == 'withdraw':
        supervisor(actor)
        Approval.objects.filter(release=release).update(withdrawn=True)
    else:
        raise ValidationError('Unknown action')
    release.save(update_fields=['commit', 'environment', 'manifest', 'fingerprint', 'state'])
    return Event.objects.create(release=release, actor=actor, key=key, action=action, payload_digest=payload)


def deploy(release_id, *, actor, key, backup_id, adapter, target_id=None):
    """An ambiguous result holds the environment lock; never automatically retry."""
    supervisor(actor)
    if not isinstance(key, str) or not key or len(key) > 100:
        raise ValidationError('Idempotency key required')
    payload = digest([release_id, actor.pk, backup_id, target_id])
    with transaction.atomic():
        old = Deployment.objects.filter(key=key).first()
        if old:
            event = Event.objects.get(key=key)
            if event.payload_digest != payload:
                raise ValidationError('Idempotency conflict')
            return old
        release = Release.objects.select_for_update().get(pk=release_id)
        if release.state not in {'merged', 'healthy', 'failed'}:
            raise ValidationError('Release is not deployable')
        if not MergeAttempt.objects.filter(release=release, state='merged', result_commit=release.commit).exists():
            raise ValidationError('A completed local merge is required')
        approval = Approval.objects.filter(release=release, fingerprint=release.fingerprint,
            environment=release.environment, withdrawn=False, actor__role='supervisor', actor__is_active=True,
            purpose='rollback' if target_id else 'deploy').order_by('-pk').first()
        if not approval:
            raise PermissionDenied('Matching owner approval required')
        if Deployment.objects.filter(approval=approval).exists():
            raise PermissionDenied('Approval already consumed; obtain a new approval')
        from operations.models import BackupRun
        backup = BackupRun.objects.get(pk=backup_id)
        if backup.state != 'success' or backup.kind == 'restore' or not backup.sha256 or not backup.size_bytes or not backup.finished_at:
            raise ValidationError('Verified completed backup required')
        target = Release.objects.get(pk=target_id) if target_id else release
        if target_id and (target.environment != release.environment or not Deployment.objects.filter(release=target, state='healthy', target__isnull=True).exists()):
            raise ValidationError('Rollback requires known healthy release in same environment')
        # Rollback approval must name the exact target through its immutable fingerprint.
        if target_id and not Approval.objects.filter(release=target, fingerprint=target.fingerprint, environment=target.environment, purpose='rollback', withdrawn=False, actor=actor).exists():
            raise PermissionDenied('Explicit target approval required')
        lock, _ = EnvironmentLock.objects.get_or_create(environment=release.environment)
        attempt = Deployment.objects.create(release=release, approval=approval, backup=backup, key=key,
            fingerprint=release.fingerprint, target=target if target_id else None)
        if not EnvironmentLock.objects.filter(pk=lock.pk, deployment__isnull=True).update(deployment=attempt):
            raise ValidationError('Environment is busy or unresolved')
        Release.objects.filter(pk=release.pk).update(state='deploying')
        Event.objects.create(release=release, actor=actor, key=key, action='rollback' if target_id else 'deploy', payload_digest=payload,
            details={'target_fingerprint': target.fingerprint})
    external = ''
    try:
        preflight = adapter.preflight(target, backup)
        if preflight != {'backup': True, 'migrations': True, 'settings': True}:
            state, external, checks = 'failed', '', {'preflight': False}
        else:
            external = adapter.publish(target, key)
            raw_checks = adapter.check(external)
            checks = {name: raw_checks.get(name) is True for name in ['health', 'pages', 'logs']}
            state = 'healthy' if checks == {'health': True, 'pages': True, 'logs': True} else 'failed'
    except Exception:
        # Do not persist exception text, CLI output or environment values.
        state, checks = 'unknown', {'requires_reconciliation': True}
    with transaction.atomic():
        Deployment.objects.filter(pk=attempt.pk).update(state=state, external_id=external, checks=checks)
        Release.objects.filter(pk=release.pk).update(state=state)
        if state != 'unknown':
            EnvironmentLock.objects.filter(deployment=attempt).update(deployment=None)
    attempt.refresh_from_db()
    return attempt
