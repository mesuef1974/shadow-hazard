"""السجل التاريخي للأتمتة (ADR 0006): البطاقة إسقاط يتحدّث، والحدث سجل لا يُعدَّل ولا يُحذف."""
from django.db import models


class Card(models.Model):
    """البطاقة: فرع عمل واحد وطلبه، من أول دفع إلى النشر."""
    branch = models.CharField('الفرع', max_length=255, unique=True)
    title = models.CharField('العنوان', max_length=300, blank=True)
    pr_number = models.PositiveIntegerField('رقم الطلب', null=True, blank=True)
    pr_url = models.URLField('رابط الطلب', blank=True)
    base = models.CharField('القاعدة', max_length=255, blank=True)
    state = models.CharField('الحالة', max_length=20, default='pushed')
    merge_sha = models.CharField('التزام الدمج', max_length=40, blank=True)
    created_at = models.DateTimeField('البداية', null=True, blank=True)
    merged_at = models.DateTimeField('الدمج', null=True, blank=True)
    closed_at = models.DateTimeField('الإغلاق', null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.branch


class EventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError('سجل الأتمتة لا يُعدَّل.')

    def delete(self):
        raise TypeError('سجل الأتمتة لا يُحذف.')


class Event(models.Model):
    STAGES = [('guard', 'حارس'), ('ci', 'فحوص'), ('pr_opened', 'فتح الطلب'),
              ('merged', 'دمج'), ('pr_closed', 'إغلاق الطلب'), ('deploy', 'نشر')]
    RESULTS = [('pass', 'نجح'), ('fail', 'فشل'), ('info', 'معلومة')]
    SOURCES = [('github', 'GitHub'), ('local', 'الجهاز')]

    external_id = models.CharField('معرّف المصدر', max_length=80, unique=True)
    stage = models.CharField('المرحلة', max_length=20, choices=STAGES, db_index=True)
    result = models.CharField('النتيجة', max_length=10, choices=RESULTS, db_index=True)
    source = models.CharField('المصدر', max_length=10, choices=SOURCES)
    card = models.ForeignKey(Card, null=True, blank=True, on_delete=models.PROTECT,
                             related_name='events')
    sha = models.CharField('الالتزام', max_length=40, blank=True)
    occurred_at = models.DateTimeField('وقت الحدث', db_index=True)
    detail = models.CharField('التفصيل', max_length=300, blank=True)
    url = models.URLField('الرابط', blank=True)
    recorded_at = models.DateTimeField('وقت التسجيل', auto_now_add=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ['-occurred_at', '-id']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError('سجل الأتمتة لا يُعدَّل.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError('سجل الأتمتة لا يُحذف.')
