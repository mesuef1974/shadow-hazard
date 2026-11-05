"""Claude token usage in normalized tables (ADR 0002, exception 6; ADR 0003 follow-up of 2026-11-04).

Only numbers, model names, branches and timestamps are stored; never conversation text.
Derived values (first/last reply, fresh tokens, totals) are computed by queries, not stored.
"""
from django.conf import settings
from django.db import models


class AiModel(models.Model):
    name = models.CharField('المعرّف', max_length=100, unique=True)
    family = models.CharField('العائلة', max_length=40, blank=True)
    provider = models.CharField('المزوّد', max_length=40, default='Anthropic')

    class Meta:
        ordering = ['name']
        verbose_name = 'نموذج'
        verbose_name_plural = 'النماذج'

    def __str__(self):
        return self.name


class GitBranch(models.Model):
    name = models.CharField('الفرع', max_length=200, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'فرع'
        verbose_name_plural = 'الفروع'

    def __str__(self):
        return self.name


class UsageSource(models.Model):
    KIND_CHOICES = [('claude_projects', 'مجلد Claude المحلي'), ('session_export', 'ملفات جلسة مصدَّرة')]
    kind = models.CharField('النوع', max_length=20, choices=KIND_CHOICES)
    path = models.CharField('المسار', max_length=1000, unique=True)
    only_this_repo = models.BooleanField('مقصور على هذا المستودع', default=True)

    class Meta:
        verbose_name = 'مصدر سجلات'
        verbose_name_plural = 'مصادر السجلات'

    def __str__(self):
        return self.path


class ImportFile(models.Model):
    """One transcript file and the byte offset already read, so imports read only new lines."""
    source = models.ForeignKey(UsageSource, on_delete=models.CASCADE, related_name='files')
    relative_path = models.CharField('المسار داخل المصدر', max_length=1000)
    offset = models.PositiveBigIntegerField(default=0)
    size = models.PositiveBigIntegerField(default=0)
    mtime = models.FloatField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'relative_path'], name='claude_usage_file_once')]
        verbose_name = 'ملف سجل'
        verbose_name_plural = 'ملفات السجلات'


class ClaudeSession(models.Model):
    SURFACE_CHOICES = [('local', 'محلية'), ('cloud', 'سحابية')]
    session_id = models.CharField('معرّف الجلسة', max_length=64, unique=True)
    project = models.ForeignKey('workspace.Project', null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='claude_sessions')
    surface = models.CharField('السطح', max_length=8, choices=SURFACE_CHOICES, default='local')
    cwd = models.CharField('مجلد العمل', max_length=500, blank=True)

    class Meta:
        verbose_name = 'جلسة Claude'
        verbose_name_plural = 'جلسات Claude'

    def __str__(self):
        return self.session_id


class ClaudeMessage(models.Model):
    """One assistant reply, unique by its API message id so re-imports never double count."""
    message_id = models.CharField('معرّف الرسالة', max_length=100, unique=True)
    session = models.ForeignKey(ClaudeSession, on_delete=models.CASCADE, related_name='messages')
    model = models.ForeignKey(AiModel, on_delete=models.PROTECT, related_name='messages')
    branch = models.ForeignKey(GitBranch, null=True, blank=True, on_delete=models.PROTECT, related_name='messages')
    file = models.ForeignKey(ImportFile, null=True, on_delete=models.SET_NULL, related_name='messages')
    at = models.DateTimeField('الوقت')
    is_subagent = models.BooleanField('وكيل فرعي', default=False)
    input_tokens = models.PositiveBigIntegerField(default=0)
    cache_creation_input_tokens = models.PositiveBigIntegerField(default=0)
    cache_read_input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ['-at']
        indexes = [models.Index(fields=['at'], name='claude_msg_at'),
                   models.Index(fields=['model', 'at'], name='claude_msg_model_at'),
                   models.Index(fields=['session', 'at'], name='claude_msg_session_at')]
        verbose_name = 'رد Claude'
        verbose_name_plural = 'ردود Claude'


class ImportRun(models.Model):
    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True)
    files_read = models.PositiveIntegerField(default=0)
    lines_read = models.PositiveIntegerField(default=0)
    messages_new = models.PositiveIntegerField(default=0)
    messages_updated = models.PositiveIntegerField(default=0)
    skipped_lines = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=False)
    error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'دفعة استيراد'
        verbose_name_plural = 'دفعات الاستيراد'


class SeatQuotaSnapshot(models.Model):
    """Seat allowance percentages typed in by the supervisor from Claude's usage page (whole seat)."""
    taken_at = models.DateTimeField('وقت القراءة', db_index=True)
    weekly_percent = models.DecimalField('الحصة الأسبوعية ٪', max_digits=4, decimal_places=1)
    session_percent = models.DecimalField('حصة الساعات الخمس ٪', max_digits=4, decimal_places=1, null=True, blank=True)
    note = models.CharField('ملاحظة', max_length=200, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-taken_at']
        constraints = [
            models.CheckConstraint(condition=models.Q(weekly_percent__gte=0, weekly_percent__lte=100), name='claude_quota_weekly_range'),
            models.CheckConstraint(condition=models.Q(session_percent__isnull=True) | models.Q(session_percent__gte=0, session_percent__lte=100), name='claude_quota_session_range'),
        ]
        verbose_name = 'لقطة حصة المقعد'
        verbose_name_plural = 'لقطات حصة المقعد'
