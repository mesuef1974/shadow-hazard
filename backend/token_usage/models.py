from django.db import models


class TrackedSession(models.Model):
    external_id = models.CharField('معرّف جلسة المصدر', max_length=100, unique=True)
    name = models.CharField('اسم الجلسة', max_length=160)
    project = models.ForeignKey('workspace.Project', on_delete=models.PROTECT)
    enabled = models.BooleanField('تضمين في المزامنة', default=True)
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
        constraints = [models.UniqueConstraint(fields=['session','source_id'],name='unique_project_token_sample')]


class SyncRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    added = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=False)
    message = models.CharField(max_length=300, blank=True)
