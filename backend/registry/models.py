import uuid
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
PROTECT = models.PROTECT
hex_validator = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "أدخل رمز لون HEX من ست خانات")
class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="المعرّف")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    class Meta:
        abstract = True
class Service(Entity):
    key = models.CharField(max_length=240, unique=True, verbose_name="المفتاح المرجعي")
    name = models.CharField(max_length=200, verbose_name="الاسم")
    kind = models.CharField(max_length=20, choices=[("skill","مهارة"),("tool","أداة"),("font","خط"),("asset","أصل"),("dependency","اعتمادية"),("client","عميل"),("model","نموذج")], verbose_name="النوع")
    purpose = models.TextField(blank=True, verbose_name="الغرض")
    state = models.CharField(max_length=20, default="discovered", choices=[("discovered","مكتشفة"),("used_in_draft","مستخدمة في مسودة"),("approved","معتمدة"),("retired","متوقفة")], verbose_name="الحالة")
    class Meta:
        verbose_name = "خدمة"
        verbose_name_plural = "الخدمات"
    def __str__(self): return self.name

class ServiceVersion(Entity):
    service = models.ForeignKey(Service, on_delete=PROTECT, verbose_name="الخدمة")
    version_label = models.CharField(max_length=200, blank=True, verbose_name="الإصدار")
    sha256 = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{64}$")], verbose_name="بصمة الملف SHA-256")
    source = models.TextField(verbose_name="المصدر")
    license_note = models.TextField(blank=True, verbose_name="الترخيص")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="البيانات الإضافية")
    class Meta:
        verbose_name = "نسخة خدمة"
        verbose_name_plural = "نسخ الخدمات"
        constraints = [models.UniqueConstraint(fields=["service","sha256"], name="unique_service_content")]
    def __str__(self): return f"{self.service} — {self.version_label or self.sha256[:12]}"

class Installation(Entity):
    external_key = models.CharField(max_length=64, unique=True, verbose_name="المعرّف الخارجي")
    version = models.ForeignKey(ServiceVersion, on_delete=PROTECT, verbose_name="نسخة الخدمة")
    root_alias = models.CharField(max_length=80, verbose_name="جذر المصدر")
    relative_path = models.TextField(verbose_name="المسار النسبي")
    observed_at = models.DateTimeField(verbose_name="وقت الرصد")
    presence = models.CharField(max_length=30, default="file_verified", verbose_name="حالة الملف", choices=[('file_verified', 'تم التحقق من الملف')])
    client_enabled = models.CharField(max_length=16, default="unknown", verbose_name="تفعيل العميل", choices=[('unknown', 'غير متحقق منه')])
    class Meta:
        verbose_name = "تثبيت مهارة"
        verbose_name_plural = "تثبيتات المهارات"
    def __str__(self): return f"{self.version.service} — {self.root_alias}"

class Actor(Entity):
    key = models.CharField(max_length=100, unique=True, verbose_name="المفتاح المرجعي")
    name = models.CharField(max_length=200, verbose_name="الاسم")
    kind = models.CharField(max_length=20, choices=[("human","شخص"),("agent","وكيل")], verbose_name="النوع")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=PROTECT, verbose_name="حساب المستخدم")
    class Meta:
        verbose_name = "منفّذ"
        verbose_name_plural = "المنفّذون"
        constraints = [models.CheckConstraint(condition=Q(kind="agent",user__isnull=True)|Q(kind="human",user__isnull=False),name="actor_identity_kind")]
    def __str__(self): return self.name

class ExecutionSession(Entity):
    external_key = models.CharField(max_length=100, unique=True, verbose_name="المعرّف الخارجي")
    name = models.CharField(max_length=200, verbose_name="الاسم")
    actor = models.ForeignKey(Actor, on_delete=PROTECT, verbose_name="المنفّذ")
    client = models.CharField(max_length=100, verbose_name="العميل")
    model = models.CharField(max_length=100, verbose_name="النموذج")
    charter_status = models.CharField(max_length=30, default="observed_not_approved", verbose_name="حالة الميثاق", choices=[('observed_not_approved', 'مرصود؛ لم يعتمد الميثاق')])
    class Meta:
        verbose_name = "جلسة تنفيذ"
        verbose_name_plural = "جلسات التنفيذ"
    def __str__(self): return self.name

class RegistryTask(Entity):
    key = models.CharField(max_length=150, unique=True, verbose_name="المفتاح المرجعي")
    title = models.CharField(max_length=240, verbose_name="العنوان")
    purpose = models.TextField(verbose_name="الغرض")
    class Meta:
        verbose_name = "مهمة سجل"
        verbose_name_plural = "مهام السجل"
    def __str__(self): return self.title

class Artifact(Entity):
    path = models.TextField(verbose_name="مسار الملف")
    sha256 = models.CharField(max_length=64, verbose_name="بصمة الملف SHA-256")
    media_type = models.CharField(max_length=100, blank=True, verbose_name="نوع الملف")
    class Meta:
        verbose_name = "ملف موثق"
        verbose_name_plural = "الملفات الموثقة"
        constraints = [models.UniqueConstraint(fields=["path","sha256"], name="unique_artifact_version")]
    def __str__(self): return self.path

class UsageEvent(Entity):
    event_key = models.CharField(max_length=150, unique=True, verbose_name="معرّف الواقعة")
    version = models.ForeignKey(ServiceVersion, on_delete=PROTECT, verbose_name="نسخة الخدمة")
    installation = models.ForeignKey(Installation, null=True, blank=True, on_delete=PROTECT, verbose_name="تثبيت المهارة")
    session = models.ForeignKey(ExecutionSession, on_delete=PROTECT, verbose_name="الجلسة")
    task = models.ForeignKey(RegistryTask, on_delete=PROTECT, verbose_name="المهمة")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت الاستخدام")
    recorded_at = models.DateTimeField(verbose_name="وقت التسجيل")
    time_evidence = models.TextField(verbose_name="دليل التوقيت")
    purpose = models.TextField(verbose_name="الغرض")
    method = models.TextField(verbose_name="طريقة الاستخدام")
    result = models.TextField(verbose_name="النتيجة")
    status = models.CharField(max_length=30, choices=[("applied_to_draft","مطبق على مسودة"),("completed","مكتمل"),("blocked","متعذر")], default="applied_to_draft", verbose_name="الحالة")
    artifacts = models.ManyToManyField(Artifact, through="UsageArtifact", verbose_name="الملفات المرتبطة")
    class Meta:
        verbose_name = "واقعة استخدام"
        verbose_name_plural = "وقائع الاستخدام"
    def __str__(self): return f"{self.version.service} — {self.purpose[:80]}"

class UsageArtifact(Entity):
    usage = models.ForeignKey(UsageEvent, on_delete=PROTECT, verbose_name="واقعة الاستخدام")
    artifact = models.ForeignKey(Artifact, on_delete=PROTECT, verbose_name="الملف الموثق")
    class Meta:
        verbose_name = "ملف استخدام"
        verbose_name_plural = "ملفات الاستخدام"
        constraints = [models.UniqueConstraint(fields=["usage","artifact"],name="unique_usage_artifact")]
    def __str__(self): return f"{self.usage} — {self.artifact}"

class BrandDirection(Entity):
    code = models.CharField(max_length=60, unique=True, verbose_name="الرمز")
    name = models.CharField(max_length=100, verbose_name="الاسم")
    english_name = models.CharField(max_length=100, verbose_name="الاسم الإنجليزي")
    story = models.TextField(verbose_name="فكرة الهوية")
    mascot_name = models.CharField(max_length=100, verbose_name="اسم التميمة")
    mascot_description = models.TextField(verbose_name="وصف التميمة")
    recommendation = models.TextField(blank=True, verbose_name="التوصية")
    status = models.CharField(max_length=20, choices=[("draft","مسودة"),("approved","معتمد"),("retired","متوقف")],default="draft", verbose_name="الحالة")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=PROTECT, verbose_name="اعتمدها")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت الاعتماد")
    class Meta:
        verbose_name = "اتجاه هوية"
        verbose_name_plural = "اتجاهات الهوية"
        constraints = [models.CheckConstraint(condition=~Q(status="approved")|Q(approved_by__isnull=False, approved_at__isnull=False),name="brand_approval_evidence")]
    def __str__(self): return self.name

class BrandColor(Entity):
    direction = models.ForeignKey(BrandDirection,on_delete=PROTECT, verbose_name="اتجاه الهوية")
    token = models.CharField(max_length=80, verbose_name="رمز اللون")
    name = models.CharField(max_length=100, verbose_name="الاسم")
    hex_value = models.CharField(max_length=7,validators=[hex_validator], verbose_name="رمز HEX")
    class Meta:
        verbose_name = "لون الهوية"
        verbose_name_plural = "ألوان الهوية"
        constraints = [models.UniqueConstraint(fields=["direction","token"],name="unique_brand_color"),models.CheckConstraint(condition=Q(hex_value__regex=r"^#[0-9A-Fa-f]{6}$"),name="valid_color_hex")]
    @property
    def rgb(self): return tuple(int(self.hex_value[i:i+2],16) for i in (1,3,5))
    def __str__(self): return f"{self.direction} — {self.name} ({self.hex_value})"

class BrandTypography(Entity):
    direction = models.ForeignKey(BrandDirection,on_delete=PROTECT, verbose_name="اتجاه الهوية")
    script = models.CharField(max_length=10,choices=[("ar","العربية"),("en","الإنجليزية")], verbose_name="اللغة")
    family = models.CharField(max_length=100, verbose_name="عائلة الخط")
    font_version = models.ForeignKey(ServiceVersion,on_delete=PROTECT, verbose_name="نسخة الخط")
    class Meta:
        verbose_name = "خط الهوية"
        verbose_name_plural = "خطوط الهوية"
        constraints = [models.UniqueConstraint(fields=["direction","script"],name="unique_brand_typography")]
    def __str__(self): return f"{self.direction} — {self.family} ({self.get_script_display()})"

class BrandAsset(Entity):
    direction = models.ForeignKey(BrandDirection,on_delete=PROTECT, verbose_name="اتجاه الهوية")
    artifact = models.ForeignKey(Artifact,on_delete=PROTECT, verbose_name="الملف الموثق")
    kind = models.CharField(max_length=50, choices=[("generated_concept_board","لوحة تصورات مولدة"),("draft_vector_symbol","رسم شعار متجهي مبدئي")], verbose_name="النوع")
    usage_rule = models.TextField(verbose_name="ضوابط الاستخدام")
    class Meta:
        verbose_name = "أصل الهوية"
        verbose_name_plural = "أصول الهوية"
        constraints = [models.UniqueConstraint(fields=["direction","artifact","kind"],name="unique_brand_asset")]
    def __str__(self): return f"{self.direction} — {self.artifact.path.rsplit(chr(47), 1)[-1]}"

class FailureRecord(Entity):
    key = models.CharField(max_length=100,unique=True, verbose_name="المفتاح المرجعي")
    task = models.ForeignKey(RegistryTask,on_delete=PROTECT, verbose_name="المهمة")
    session = models.ForeignKey(ExecutionSession,on_delete=PROTECT, verbose_name="الجلسة")
    symptom = models.TextField(verbose_name="وصف المشكلة")
    cause = models.TextField(verbose_name="السبب")
    correction = models.TextField(verbose_name="الإجراء التصحيحي")
    prevention = models.TextField(verbose_name="منع التكرار")
    evidence = models.TextField(verbose_name="الدليل")
    class Meta:
        verbose_name = "مشكلة موثقة"
        verbose_name_plural = "المشكلات الموثقة"
    def __str__(self): return self.symptom[:100]

class RegistryEvent(Entity):
    key = models.CharField(max_length=150,unique=True, verbose_name="المفتاح المرجعي")
    session = models.ForeignKey(ExecutionSession,on_delete=PROTECT, verbose_name="الجلسة")
    action = models.CharField(max_length=100, verbose_name="الإجراء")
    summary = models.JSONField(default=dict, verbose_name="التفاصيل")
    class Meta:
        verbose_name = "حدث سجل"
        verbose_name_plural = "أحداث السجل"
    def __str__(self): return f"{self.action} — {self.key}"
class PythonPackage(Entity):
    name = models.CharField('اسم المكتبة', max_length=200, unique=True)
    version = models.CharField('الإصدار', max_length=100)
    summary = models.TextField('الوصف', blank=True)
    requires = models.JSONField('الاعتماديات المعلنة', default=list)
    location = models.TextField('مسار التثبيت')
    python_version = models.CharField('إصدار Python', max_length=100)
    environment = models.TextField('البيئة الافتراضية')
    is_installed = models.BooleanField('مثبتة حاليًا', default=True)
    observed_at = models.DateTimeField('آخر جرد')
    class Meta:
        verbose_name = 'مكتبة Python'
        verbose_name_plural = 'مكتبات Python'
    def __str__(self): return f'{self.name} — {self.version}'
