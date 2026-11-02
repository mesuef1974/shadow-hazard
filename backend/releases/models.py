from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError


class Release(models.Model):
    creation_key = models.CharField(max_length=100, unique=True, null=True, editable=False)
    creation_digest = models.CharField(max_length=64, blank=True, editable=False)
    task = models.ForeignKey('workspace.Task', null=True, on_delete=models.PROTECT)
    job = models.ForeignKey('workspace.ModelJob', null=True, on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    commit = models.CharField(max_length=64)
    repository = models.CharField(max_length=80, default='')
    source_ref = models.CharField(max_length=200, default='')
    target_branch = models.CharField(max_length=200, default='')
    target_commit = models.CharField(max_length=64, default='')
    environment = models.CharField(max_length=80)
    manifest = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default='draft', choices=[(s, s) for s in ['draft', 'pinned', 'merging', 'merge_failed', 'merged', 'deploying', 'healthy', 'failed', 'unknown']])
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            if old.creation_key != self.creation_key or old.creation_digest != self.creation_digest:
                raise ValidationError('Creation receipt is immutable')
            if old.state != 'draft' and any(getattr(old, f) != getattr(self, f) for f in ['commit', 'environment', 'manifest', 'fingerprint', 'author_id', 'task_id', 'job_id', 'repository', 'source_ref', 'target_branch', 'target_commit']):
                raise ValidationError('Pinned release identity is immutable')
        super().save(*args, **kwargs)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(task__isnull=False, job__isnull=True) | models.Q(task__isnull=True, job__isnull=False)), name='release_exactly_one_source')]


class Evidence(models.Model):
    release = models.ForeignKey(Release, on_delete=models.PROTECT, related_name='evidence')
    fingerprint = models.CharField(max_length=64)
    kind = models.CharField(max_length=24)
    reference = models.CharField(max_length=500)
    accepted = models.BooleanField(default=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class Review(models.Model):
    release = models.ForeignKey(Release, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    accepted = models.BooleanField()


class Approval(models.Model):
    release = models.ForeignKey(Release, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    environment = models.CharField(max_length=80)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    purpose = models.CharField(max_length=16, default='deploy')
    withdrawn = models.BooleanField(default=False)


class Deployment(models.Model):
    release = models.ForeignKey(Release, on_delete=models.PROTECT)
    approval = models.ForeignKey(Approval, on_delete=models.PROTECT)
    backup = models.ForeignKey('operations.BackupRun', on_delete=models.PROTECT)
    target = models.ForeignKey(Release, null=True, on_delete=models.PROTECT, related_name='rollback_attempts')
    key = models.CharField(max_length=100, unique=True)
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default='running')
    external_id = models.CharField(max_length=200, blank=True)
    checks = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class EnvironmentLock(models.Model):
    environment = models.CharField(max_length=80, unique=True)
    deployment = models.OneToOneField(Deployment, null=True, on_delete=models.PROTECT)


class Event(models.Model):
    release = models.ForeignKey(Release, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=100, unique=True)
    action = models.CharField(max_length=32)
    payload_digest = models.CharField(max_length=64)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class MergeAttempt(models.Model):
    release = models.ForeignKey(Release, on_delete=models.PROTECT)
    key = models.CharField(max_length=100, unique=True)
    state = models.CharField(max_length=24, default='running')
    result_commit = models.CharField(max_length=64, blank=True)


class RepositoryLock(models.Model):
    repository = models.CharField(max_length=80, unique=True)
    attempt = models.OneToOneField(MergeAttempt, null=True, on_delete=models.PROTECT)
