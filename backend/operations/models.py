from django.db import models
from django.conf import settings


class OperatorMonitorPolicy(models.Model):
    project=models.OneToOneField('workspace.Project',on_delete=models.PROTECT)
    charter=models.OneToOneField('workspace.SessionCharter',on_delete=models.PROTECT)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    enabled=models.BooleanField(default=False)
    activation_receipt=models.ForeignKey('workspace.ProjectDocumentation',null=True,on_delete=models.PROTECT)
    cooldown_seconds=models.PositiveIntegerField(default=300)
    scopes=models.JSONField(default=list)
    last_sent_at=models.DateTimeField(null=True)
    last_error=models.CharField(max_length=80,blank=True)
    updated_at=models.DateTimeField(auto_now=True)

    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(cooldown_seconds__gte=120,cooldown_seconds__lte=86400),name='operator_cooldown_bounds')]


class OperatorIncident(models.Model):
    policy=models.ForeignKey(OperatorMonitorPolicy,on_delete=models.PROTECT)
    condition_key=models.CharField(max_length=120)
    generation=models.PositiveIntegerField(default=1)
    code=models.CharField(max_length=80)
    reference=models.JSONField(default=dict)
    state=models.CharField(max_length=12,default='open',choices=[('open','open'),('resolved','resolved')])
    job=models.ForeignKey('workspace.ModelJob',null=True,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    resolved_at=models.DateTimeField(null=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['policy','condition_key','generation'],name='operator_incident_generation')]


def review_document_query(job_ids):
    query = models.Q(pk__in=[])
    for job_id in job_ids:
        base = f'automatic-review:{job_id}'
        query |= models.Q(task_key=base) | models.Q(task_key__startswith=base + ':revision:')
    return query


class ReviewAssignment(models.Model):
    source_job=models.OneToOneField('workspace.ModelJob',on_delete=models.PROTECT,related_name='automatic_review',verbose_name='الطلب الأصلي')
    review_job=models.ForeignKey('workspace.ModelJob',on_delete=models.PROTECT,related_name='reviewed_assignments',verbose_name='طلب المراجعة')
    created_at=models.DateTimeField(auto_now_add=True,verbose_name='وقت الإسناد')

    class Meta:
        verbose_name='إسناد مراجعة تلقائي'
        verbose_name_plural='إسنادات المراجعة التلقائية'

    @property
    def state(self):return self.review_job.state

    @property
    def result_document(self):
        from workspace.models import ProjectDocumentation
        return ProjectDocumentation.objects.filter(review_document_query([self.source_job_id]),
            project_id=self.source_job.charter.project_id,session_key='review').order_by('-created_at','-pk').first()


class AutomationIssue(models.Model):
    STATES = [("open", "مفتوح"), ("resolved", "تمت المعالجة")]
    SEVERITIES = [("info", "معلومة"), ("warning", "تحذير"), ("critical", "حرج")]

    job = models.ForeignKey(
        "workspace.ModelJob", null=True, blank=True, on_delete=models.PROTECT,
        related_name="automation_issues", verbose_name="المهمة",
    )
    fingerprint = models.CharField(max_length=64, unique=True, verbose_name="بصمة العائق")
    code = models.CharField(max_length=60, verbose_name="رمز العائق")
    severity = models.CharField(max_length=12, choices=SEVERITIES, default="warning", verbose_name="الخطورة")
    state = models.CharField(max_length=12, choices=STATES, default="open", verbose_name="الحالة")
    title = models.CharField(max_length=200, verbose_name="العنوان")
    evidence = models.JSONField(default=dict, verbose_name="الأدلة")
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name="أول رصد")
    last_seen = models.DateTimeField(auto_now=True, verbose_name="آخر رصد")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت المعالجة")

    class Meta:
        ordering = ["state", "-last_seen", "pk"]
        verbose_name = "عائق أتمتة"
        verbose_name_plural = "عوائق الأتمتة"
        indexes = [models.Index(fields=["state", "code"], name="ops_issue_state_code")]

    def __str__(self):
        return f"{self.get_severity_display()} — {self.title}"


class BackupPolicy(models.Model):
    key = models.CharField(max_length=40, unique=True, default="default", verbose_name="المفتاح")
    enabled = models.BooleanField(default=True, verbose_name="مفعّلة")
    interval_hours = models.PositiveSmallIntegerField(default=24, verbose_name="الفاصل بالساعات")
    retained_successful = models.PositiveSmallIntegerField(default=7, verbose_name="عدد النسخ الناجحة المحتفظ بها")
    before_migration = models.BooleanField(default=True, verbose_name="نسخة قبل الهجرة")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تعديل")

    class Meta:
        verbose_name = "سياسة نسخ احتياطي"
        verbose_name_plural = "سياسات النسخ الاحتياطي"
        constraints = [
            models.CheckConstraint(condition=models.Q(interval_hours__gte=1), name="ops_backup_interval_positive"),
            models.CheckConstraint(condition=models.Q(retained_successful__gte=1), name="ops_backup_retention_positive"),
        ]

    def __str__(self):
        return f"{self.key} — كل {self.interval_hours} ساعة"


class BackupRun(models.Model):
    KINDS = [("scheduled", "دوري"), ("manual", "يدوي"), ("pre_migration", "قبل الهجرة"), ("restore", "استعادة منفصلة")]
    STATES = [("running", "جارٍ"), ("success", "ناجح"), ("failed", "فاشل")]

    kind = models.CharField(max_length=20, choices=KINDS, verbose_name="النوع")
    state = models.CharField(max_length=12, choices=STATES, default="running", verbose_name="الحالة")
    relative_path = models.CharField(max_length=300, blank=True, verbose_name="المسار داخل .local")
    sha256 = models.CharField(max_length=64, blank=True, verbose_name="بصمة SHA-256")
    size_bytes = models.PositiveBigIntegerField(default=0, verbose_name="الحجم بالبايت")
    integrity_result = models.CharField(max_length=20, blank=True, verbose_name="فحص SQLite")
    source = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="restorations", verbose_name="مصدر الاستعادة")
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="بدأ في")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="انتهى في")
    error_code = models.CharField(max_length=40, blank=True, verbose_name="رمز الخطأ")
    details = models.JSONField(default=dict, blank=True, verbose_name="تفاصيل التحقق")

    class Meta:
        ordering = ["-started_at", "-pk"]
        verbose_name = "عملية نسخ أو استعادة"
        verbose_name_plural = "عمليات النسخ والاستعادة"
        indexes = [models.Index(fields=["kind", "state", "finished_at"], name="ops_backup_kind_state")]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.get_state_display()} #{self.pk}"
