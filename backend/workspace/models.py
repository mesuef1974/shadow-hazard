from django.db import models
from django.conf import settings

class Project(models.Model):
    name = models.CharField('اسم المشروع', max_length=160)
    summary = models.TextField('الملخص', blank=True)
    def __str__(self): return self.name

class Phase(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='phases')
    title = models.CharField('المرحلة', max_length=180)
    objective = models.TextField('الهدف ومخرجات القبول')
    order = models.PositiveIntegerField('الترتيب')
    class Meta: ordering = ['order', 'pk']
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
    class Meta: ordering = ['-created_at']
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
        constraints = [models.CheckConstraint(condition=models.Q(hours__isnull=True)|models.Q(hours__gte=0),name='workspace_nonnegative_hours')]

class Decision(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    title = models.CharField('عنوان القرار', max_length=200)
    body = models.TextField('القرار ومبرراته')
    private = models.BooleanField('خاص بالمشرف', default=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at']

class DocumentAccess(models.Model):
    path = models.CharField('مسار الوثيقة داخل docs', max_length=500, unique=True)
    team_visible = models.BooleanField('متاح للفريق', default=False)
    def __str__(self): return self.path


class ModelPolicy(models.Model):
    category = models.CharField('فئة المهمة',max_length=60,unique=True)
    title = models.CharField('التسمية',max_length=160)
    model = models.CharField('النموذج المطلوب',max_length=80,default='gpt-6-luna')
    thinking = models.CharField('التفكير',max_length=20,default='low')
    max_attempts = models.PositiveSmallIntegerField('حد المحاولات المقترح',default=2)
    enabled = models.BooleanField(default=True)
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
    lane = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    result = models.TextField(blank=True)
    observed_model = models.CharField(max_length=80,blank=True)
    outcome_note = models.TextField(blank=True)
    class Meta:
        ordering=['-created_at']
        constraints=[models.UniqueConstraint(fields=['lane'],condition=models.Q(state__in=['running','uncertain']),name='one_local_model_job_running')]
