from django.db import models


class TrackedSession(models.Model):
    external_id = models.CharField('معرّف جلسة المصدر', max_length=100, unique=True)
    name = models.CharField('اسم الجلسة', max_length=160)
    project = models.ForeignKey('workspace.Project', on_delete=models.PROTECT)
    enabled = models.BooleanField('تضمين في المزامنة', default=True)
    class Meta:
        verbose_name = 'جلسة متتبعة'
        verbose_name_plural = 'الجلسات المتتبعة'
    def __str__(self): return self.name


class TokenSample(models.Model):
    session = models.ForeignKey(TrackedSession, on_delete=models.PROTECT, related_name='samples')
    source_id = models.PositiveBigIntegerField('معرف العينة بالمصدر')
    sampled_at = models.DateTimeField('وقت الرصد')
    model = models.CharField('النموذج', max_length=160, blank=True)
    effort = models.CharField('مستوى الاستدلال', max_length=40, blank=True)
    tier = models.CharField('نوع الخدمة', max_length=40, blank=True)
    total = models.PositiveBigIntegerField('الإجمالي التراكمي')
    input = models.PositiveBigIntegerField('الإدخال التراكمي')
    cached = models.PositiveBigIntegerField('الإدخال المخزن مؤقتًا')
    output = models.PositiveBigIntegerField('الإخراج التراكمي')
    reasoning = models.PositiveBigIntegerField('الاستدلال ضمن الإخراج')
    context = models.PositiveBigIntegerField('السياق الحالي')
    context_limit = models.PositiveBigIntegerField('سعة السياق')
    imported_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['sampled_at', 'source_id']
        verbose_name = 'عينة رموز'
        verbose_name_plural = 'عينات الرموز'
        indexes = [models.Index(fields=['session', 'sampled_at'], name='token_sample_session_time')]
        constraints = [
            models.UniqueConstraint(fields=['session','source_id'],name='unique_project_token_sample'),
            models.CheckConstraint(condition=models.Q(total=models.F('input') + models.F('output')), name='token_total_matches_parts'),
            models.CheckConstraint(condition=models.Q(cached__lte=models.F('input')), name='token_cached_lte_input'),
            models.CheckConstraint(condition=models.Q(reasoning__lte=models.F('output')), name='token_reasoning_lte_output'),
        ]


class SyncRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    added = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=False)
    message = models.CharField(max_length=300, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    class Meta:
        verbose_name = 'عملية مزامنة'
        verbose_name_plural = 'عمليات المزامنة'


class CollectorState(models.Model):
    key = models.CharField(max_length=40, unique=True, default='local')
    payload = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        verbose_name = 'حالة جامع البيانات'
        verbose_name_plural = 'حالة جامع البيانات'


class ContextSource(models.Model):
    key = models.CharField(max_length=40, unique=True, default='codex-runtime')
    generation = models.UUIDField()
    identity = models.CharField(max_length=64)
    schema_digest = models.CharField(max_length=64, blank=True)
    highwater = models.PositiveBigIntegerField(default=0)
    anchor_digest = models.CharField(max_length=64, blank=True)
    cursors = models.JSONField(default=dict)
    status = models.CharField(max_length=32, default='unavailable')
    checked_at = models.DateTimeField()


class ContextObservation(models.Model):
    key = models.CharField(max_length=64, unique=True)
    charter = models.ForeignKey('workspace.SessionCharter', on_delete=models.PROTECT)
    thread_id = models.CharField(max_length=100)
    turn_id = models.UUIDField()
    generation = models.UUIDField()
    process_uuid = models.CharField(max_length=100)
    source_row = models.PositiveBigIntegerField()
    source_digest = models.CharField(max_length=64)
    parser_version = models.CharField(max_length=80)
    sampled_at = models.DateTimeField()
    imported_at = models.DateTimeField(auto_now_add=True)
    current = models.PositiveBigIntegerField()
    limit = models.PositiveBigIntegerField()
    model = models.CharField(max_length=100, blank=True)

    class Meta:
        indexes = [models.Index(fields=['charter', 'thread_id', 'sampled_at'], name='context_binding_time')]


class CapacityPolicy(models.Model):
    version = models.CharField(max_length=80, unique=True)
    warning_percent = models.PositiveSmallIntegerField(default=70)
    prepare_percent = models.PositiveSmallIntegerField(default=85)
    warning_reset = models.PositiveSmallIntegerField(default=65)
    prepare_reset = models.PositiveSmallIntegerField(default=80)
    max_age_seconds = models.PositiveIntegerField(default=120)
    sync_interval_seconds = models.PositiveIntegerField(default=60)
    freshness_basis = models.CharField(max_length=160, default='120s = two 60s synchronization cycles; both sample and latest successful sync must be fresh')
    source_semantics = models.CharField(max_length=200, default='usage_samples.context_total_tokens/context_window_tokens; current context, not cumulative input')
    source_kind = models.CharField(max_length=32, default='last_request')
    parser_version = models.CharField(max_length=80, blank=True)
    source_schema_digest = models.CharField(max_length=64, blank=True)
    thresholds_enabled = models.BooleanField(default=False)
    validation = models.ForeignKey('workspace.ProjectDocumentation', null=True, on_delete=models.PROTECT, related_name='validated_capacity_policies')
    decision = models.ForeignKey('workspace.ProjectDocumentation', on_delete=models.PROTECT)
    decided_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT)
    decided_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            from django.core.exceptions import ValidationError
            raise ValidationError('Create a new policy version instead of changing an existing version')
        super().save(*args, **kwargs)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(warning_reset__lt=models.F('warning_percent'), warning_percent__lt=models.F('prepare_reset'), prepare_reset__lt=models.F('prepare_percent'), prepare_percent__lte=100, max_age_seconds__gt=0), name='capacity_valid_thresholds')]


class CapacityState(models.Model):
    charter = models.ForeignKey('workspace.SessionCharter', on_delete=models.PROTECT)
    policy = models.ForeignKey(CapacityPolicy, on_delete=models.PROTECT)
    thread_id = models.CharField(max_length=100)
    warning_armed = models.BooleanField(default=True)
    prepare_armed = models.BooleanField(default=True)
    warning_cycle = models.PositiveIntegerField(default=0)
    prepare_cycle = models.PositiveIntegerField(default=0)
    revision = models.PositiveIntegerField(default=0)
    last_observation = models.ForeignKey(ContextObservation, null=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['charter','policy','thread_id'], name='capacity_state_binding')]


class CapacityMeasurement(models.Model):
    key = models.CharField(max_length=64, unique=True)
    state = models.ForeignKey(CapacityState, on_delete=models.PROTECT)
    sample = models.ForeignKey(TokenSample, null=True, on_delete=models.PROTECT)
    sync = models.ForeignKey(SyncRun, null=True, on_delete=models.PROTECT)
    observation = models.ForeignKey(ContextObservation, null=True, on_delete=models.PROTECT)
    context_source = models.ForeignKey(ContextSource, null=True, on_delete=models.PROTECT)
    source_generation = models.UUIDField(null=True)
    source_checked_at = models.DateTimeField(null=True)
    source_schema_digest = models.CharField(max_length=64, blank=True)
    evidence = models.JSONField(default=dict)
    trusted = models.BooleanField()
    reason = models.CharField(max_length=80, blank=True)
    current = models.PositiveBigIntegerField(null=True)
    limit = models.PositiveBigIntegerField(null=True)
    percent = models.FloatField(null=True)
    observed_at = models.DateTimeField()


class CapacityEvent(models.Model):
    key = models.CharField(max_length=100, unique=True)
    measurement = models.ForeignKey(CapacityMeasurement, on_delete=models.PROTECT)
    kind = models.CharField(max_length=24, choices=[('warning','warning'),('prepare','prepare')])
    created_at = models.DateTimeField(auto_now_add=True)


class CapacityHandover(models.Model):
    event = models.OneToOneField(CapacityEvent, on_delete=models.PROTECT)
    observation = models.ForeignKey(ContextObservation, null=True, on_delete=models.PROTECT)
    payload = models.JSONField()
    digest = models.CharField(max_length=64)
    receipt = models.OneToOneField('workspace.ProjectDocumentation', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = 'حالة جامع البيانات'
        verbose_name_plural = 'حالة جامع البيانات'
