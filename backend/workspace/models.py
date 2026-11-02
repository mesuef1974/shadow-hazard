from django.db import models
from django.conf import settings

class ProjectMemory(models.Model):
    project=models.ForeignKey('Project',on_delete=models.PROTECT)
    key=models.CharField(max_length=100)
    instruction=models.TextField()
    enabled=models.BooleanField(default=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['project','key'],name='project_memory_key')]


class ProjectDocumentation(models.Model):
    project=models.ForeignKey('Project',on_delete=models.PROTECT)
    session_key=models.CharField(max_length=100)
    task_key=models.CharField(max_length=160)
    summary=models.TextField()
    evidence=models.JSONField(default=dict)
    digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['project','session_key','task_key'],name='project_documentation_once')]

class Project(models.Model):
    name = models.CharField('اسم المشروع', max_length=160)
    summary = models.TextField('الملخص', blank=True)
    class Meta:
        verbose_name = 'مشروع'
        verbose_name_plural = 'المشاريع'
    def __str__(self): return self.name

class Phase(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='phases')
    title = models.CharField('المرحلة', max_length=180)
    objective = models.TextField('الهدف ومخرجات القبول')
    order = models.PositiveIntegerField('الترتيب')
    class Meta:
        ordering = ['order', 'pk']
        verbose_name = 'مرحلة'
        verbose_name_plural = 'المراحل'
    def __str__(self): return self.title

class Task(models.Model):
    STATES = [('todo','لم تبدأ'),('doing','قيد التنفيذ'),('review','للمراجعة'),('accepted','معتمدة')]
    phase = models.ForeignKey(Phase, on_delete=models.PROTECT, related_name='tasks')
    title = models.CharField('عنوان المهمة', max_length=200)
    description = models.TextField('المطلوب ومعيار القبول')
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='المسؤول', related_name='project_tasks')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    state = models.CharField('الحالة', max_length=16, choices=STATES, default='todo')
    due_date = models.DateField('الموعد المستهدف', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'مهمة'
        verbose_name_plural = 'المهام'
    def __str__(self): return self.title

class TaskUpdate(models.Model):
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name='updates')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField('التحديث والدليل')
    state = models.CharField(max_length=16, choices=Task.STATES)
    hours = models.DecimalField('ساعات العمل (اختياري)', max_digits=6, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'تحديث مهمة'
        verbose_name_plural = 'تحديثات المهام'
        constraints = [models.CheckConstraint(condition=models.Q(hours__isnull=True)|models.Q(hours__gte=0),name='workspace_nonnegative_hours')]

class Decision(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    title = models.CharField('عنوان القرار', max_length=200)
    body = models.TextField('القرار ومبرراته')
    private = models.BooleanField('خاص بالمشرف', default=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'قرار'
        verbose_name_plural = 'القرارات'

class DocumentAccess(models.Model):
    path = models.CharField('مسار الوثيقة داخل docs', max_length=500, unique=True)
    team_visible = models.BooleanField('متاح للفريق', default=False)
    class Meta:
        verbose_name = 'صلاحية وثيقة'
        verbose_name_plural = 'صلاحيات الوثائق'
    def __str__(self): return self.path


class ModelPolicy(models.Model):
    category = models.CharField('فئة المهمة',max_length=60,unique=True)
    title = models.CharField('التسمية',max_length=160)
    model = models.CharField('النموذج المطلوب',max_length=80,default='gpt-6-luna')
    thinking = models.CharField('التفكير',max_length=20,default='low')
    max_attempts = models.PositiveSmallIntegerField('حد المحاولات المقترح',default=2)
    enabled = models.BooleanField(default=True)
    class Meta:
        verbose_name = 'سياسة نموذج'
        verbose_name_plural = 'سياسات النماذج'
    def __str__(self):return self.title


class SessionCharter(models.Model):
    key = models.CharField(max_length=60,unique=True)
    name = models.CharField('اسم الجلسة',max_length=160)
    project = models.ForeignKey(Project,on_delete=models.PROTECT)
    policy = models.ForeignKey(ModelPolicy,on_delete=models.PROTECT)
    scope = models.TextField('نطاق الملكية')
    rules = models.TextField('الميثاق')
    thread_id = models.CharField('معرف محادثة Codex',max_length=100,blank=True)
    state = models.CharField('الحالة',max_length=40,default='prepared')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = 'ميثاق جلسة'
        verbose_name_plural = 'مواثيق الجلسات'
    def __str__(self):return self.name


class ModelJob(models.Model):
    submission_key = models.UUIDField(null=True, unique=True, editable=False)
    charter = models.ForeignKey(SessionCharter,on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    prompt = models.TextField('المهمة المحددة')
    requested_model = models.CharField(max_length=80)
    requested_thinking = models.CharField(max_length=20)
    mode = models.CharField(max_length=20,default='read-only')
    state = models.CharField(max_length=30,default='queued')
    lane = models.PositiveIntegerField(default=1, verbose_name='مسار الجلسة')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    result = models.TextField(blank=True)
    observed_model = models.CharField(max_length=80,blank=True)
    execution_thread_id = models.CharField(max_length=100,blank=True)
    token_usage = models.JSONField(default=dict,blank=True)
    outcome_note = models.TextField(blank=True)
    class Meta:
        ordering=['-created_at']
        verbose_name = 'طلب تشغيل نموذج'
        verbose_name_plural = 'طلبات تشغيل النماذج'
        indexes = [models.Index(fields=['execution_thread_id'], name='modeljob_thread_lookup')]
        constraints=[models.UniqueConstraint(fields=['lane'],condition=models.Q(state__in=['running','uncertain']),name='one_active_model_job_per_lane')]


class DispatcherHeartbeat(models.Model):
    key = models.CharField(max_length=20,unique=True,default='local')
    updated_at = models.DateTimeField(auto_now=True)
    pid = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=20,default='stopped')
    started_at = models.DateTimeField(null=True)
    stopped_at = models.DateTimeField(null=True)
    startup_method = models.CharField(max_length=60,blank=True)
    last_error = models.CharField(max_length=200,blank=True)
    class Meta:
        verbose_name = 'حالة موزّع المهام'
        verbose_name_plural = 'حالة موزّع المهام'


class DispatcherRun(models.Model):
    pid = models.PositiveIntegerField()
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    state = models.CharField(max_length=20,default='running')
    class Meta:
        verbose_name = 'تشغيل موزّع'
        verbose_name_plural = 'سجل تشغيل الموزّع'


class SessionRenewal(models.Model):
    """Manual renewal saga. No automatic dispatcher consumes these requests."""
    key = models.UUIDField(unique=True, editable=False)
    charter = models.ForeignKey(SessionCharter, on_delete=models.PROTECT, related_name='renewals')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source_thread_id = models.CharField(max_length=100)
    target_thread_id = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=30, default='prepared')
    handover = models.ForeignKey(ProjectDocumentation, on_delete=models.PROTECT, related_name='+')
    handover_digest = models.CharField(max_length=64)
    bootstrap = models.ForeignKey(ProjectDocumentation, on_delete=models.PROTECT, related_name='+')
    bootstrap_prompt = models.TextField()
    bootstrap_digest = models.CharField(max_length=64)
    context = models.JSONField(default=dict)
    approval = models.ForeignKey(ProjectDocumentation, null=True, on_delete=models.PROTECT, related_name='+')
    claim_token = models.UUIDField(null=True)
    create_attempted = models.BooleanField(default=False)
    creation_receipt = models.ForeignKey(ProjectDocumentation, null=True, on_delete=models.PROTECT, related_name='+')
    delivery_receipt = models.ForeignKey(ProjectDocumentation, null=True, on_delete=models.PROTECT, related_name='+')
    binding_receipt = models.ForeignKey(ProjectDocumentation, null=True, on_delete=models.PROTECT, related_name='+')
    rollback_receipt = models.ForeignKey(ProjectDocumentation, null=True, on_delete=models.PROTECT, related_name='+')
    activity = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['charter', 'source_thread_id'],
                condition=models.Q(create_attempted=True) | ~models.Q(state='cancelled'), name='renewal_source_once'),
            models.UniqueConstraint(fields=['target_thread_id'], condition=~models.Q(target_thread_id=''),
                                    name='renewal_target_once'),
        ]


class SessionRenewalEvent(models.Model):
    renewal = models.ForeignKey(SessionRenewal, on_delete=models.PROTECT, related_name='events')
    state = models.CharField(max_length=30)
    receipt = models.ForeignKey(ProjectDocumentation, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class AutoRenewalPolicy(models.Model):
    """Per-session automatic renewal gate; default-off and owner-receipt bound."""
    charter = models.OneToOneField(SessionCharter, on_delete=models.PROTECT, related_name='auto_renewal_policy')
    enabled = models.BooleanField(default=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    activation_receipt = models.ForeignKey(ProjectDocumentation, null=True, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'سياسة التجديد الآلي'
        verbose_name_plural = 'سياسات التجديد الآلي'


class AutoRenewalLatch(models.Model):
    STATES = [
        ('latched_disabled', 'مرصود والسياسة معطلة'), ('ready', 'جاهز بعد موافقة صريحة'),
        ('claimed', 'محجوز للتنفيذ'), ('preintent_backoff', 'تأجيل قبل نية الإنشاء'),
        ('owner_intent', 'نية إنشاء دائمة'), ('candidate_known', 'مرشح معروف'),
        ('ambiguous', 'نتيجة إنشاء ملتبسة'), ('verified', 'تم التحقق'),
        ('switched', 'تم تبديل الربط'), ('stale', 'دليل متقادم'),
        ('invalidated', 'تغير ربط المصدر'), ('quarantined', 'محجور للمراجعة'),
        ('blocked', 'متعذر ضمن الميزانية'),
    ]
    charter = models.ForeignKey(SessionCharter, on_delete=models.PROTECT, related_name='auto_renewal_latches')
    source_thread_id = models.CharField(max_length=100)
    handover = models.ForeignKey('token_usage.CapacityHandover', on_delete=models.PROTECT)
    observation = models.ForeignKey('token_usage.ContextObservation', on_delete=models.PROTECT)
    request_key = models.UUIDField(unique=True, editable=False)
    state = models.CharField(max_length=24, choices=STATES, default='latched_disabled')
    evidence = models.JSONField(default=dict)
    evidence_digest = models.CharField(max_length=64)
    claim_token = models.UUIDField(null=True)
    owner_intent_receipt = models.ForeignKey(ProjectDocumentation, null=True, on_delete=models.PROTECT, related_name='+')
    candidate_thread_id = models.CharField(max_length=100, blank=True)
    renewal = models.OneToOneField(SessionRenewal, null=True, on_delete=models.PROTECT, related_name='auto_latch')
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['charter', 'source_thread_id'], name='auto_renewal_source_once'),
            models.UniqueConstraint(fields=['handover'], name='auto_renewal_handover_once'),
        ]
        indexes = [models.Index(fields=['state', 'next_attempt_at'], name='auto_renewal_due')]
