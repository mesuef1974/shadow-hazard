import uuid
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
PROTECT = models.PROTECT
hex_validator = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use a six-digit HEX color")
class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        abstract = True
class Service(Entity):
    key = models.CharField(max_length=240, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=[(v,v) for v in ["skill","tool","font","asset","dependency","client","model"]])
    purpose = models.TextField(blank=True)
    state = models.CharField(max_length=20, default="discovered", choices=[(v,v) for v in ["discovered","used_in_draft","approved","retired"]])
    def __str__(self): return self.key
class ServiceVersion(Entity):
    service = models.ForeignKey(Service, on_delete=PROTECT)
    version_label = models.CharField(max_length=200, blank=True)
    sha256 = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{64}$")])
    source = models.TextField()
    license_note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["service","sha256"], name="unique_service_content")]
class Installation(Entity):
    external_key = models.CharField(max_length=64, unique=True)
    version = models.ForeignKey(ServiceVersion, on_delete=PROTECT)
    root_alias = models.CharField(max_length=80)
    relative_path = models.TextField()
    observed_at = models.DateTimeField()
    presence = models.CharField(max_length=30, default="file_verified")
    client_enabled = models.CharField(max_length=16, default="unknown")
class Actor(Entity):
    key = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=[("human","human"),("agent","agent")])
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=PROTECT)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(kind="agent",user__isnull=True)|Q(kind="human",user__isnull=False),name="actor_identity_kind")]
class ExecutionSession(Entity):
    external_key = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    actor = models.ForeignKey(Actor, on_delete=PROTECT)
    client = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    charter_status = models.CharField(max_length=30, default="observed_not_approved")
class RegistryTask(Entity):
    key = models.CharField(max_length=150, unique=True)
    title = models.CharField(max_length=240)
    purpose = models.TextField()
class Artifact(Entity):
    path = models.TextField()
    sha256 = models.CharField(max_length=64)
    media_type = models.CharField(max_length=100, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["path","sha256"], name="unique_artifact_version")]
class UsageEvent(Entity):
    event_key = models.CharField(max_length=150, unique=True)
    version = models.ForeignKey(ServiceVersion, on_delete=PROTECT)
    installation = models.ForeignKey(Installation, null=True, blank=True, on_delete=PROTECT)
    session = models.ForeignKey(ExecutionSession, on_delete=PROTECT)
    task = models.ForeignKey(RegistryTask, on_delete=PROTECT)
    used_at = models.DateTimeField(null=True, blank=True)
    recorded_at = models.DateTimeField()
    time_evidence = models.TextField()
    purpose = models.TextField()
    method = models.TextField()
    result = models.TextField()
    status = models.CharField(max_length=30, default="applied_to_draft")
    artifacts = models.ManyToManyField(Artifact, through="UsageArtifact")
class UsageArtifact(Entity):
    usage = models.ForeignKey(UsageEvent, on_delete=PROTECT)
    artifact = models.ForeignKey(Artifact, on_delete=PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["usage","artifact"],name="unique_usage_artifact")]
class BrandDirection(Entity):
    code = models.CharField(max_length=60, unique=True)
    name = models.CharField(max_length=100)
    english_name = models.CharField(max_length=100)
    story = models.TextField()
    mascot_name = models.CharField(max_length=100)
    mascot_description = models.TextField()
    recommendation = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=[("draft","draft"),("approved","approved"),("retired","retired")],default="draft")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=PROTECT)
    approved_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(status="approved")|Q(approved_by__isnull=False, approved_at__isnull=False),name="brand_approval_evidence")]
class BrandColor(Entity):
    direction = models.ForeignKey(BrandDirection,on_delete=PROTECT)
    token = models.CharField(max_length=80)
    name = models.CharField(max_length=100)
    hex_value = models.CharField(max_length=7,validators=[hex_validator])
    class Meta:
        constraints = [models.UniqueConstraint(fields=["direction","token"],name="unique_brand_color"),models.CheckConstraint(condition=Q(hex_value__regex=r"^#[0-9A-Fa-f]{6}$"),name="valid_color_hex")]
    @property
    def rgb(self): return tuple(int(self.hex_value[i:i+2],16) for i in (1,3,5))
class BrandTypography(Entity):
    direction = models.ForeignKey(BrandDirection,on_delete=PROTECT)
    script = models.CharField(max_length=10,choices=[("ar","Arabic"),("en","English")])
    family = models.CharField(max_length=100)
    font_version = models.ForeignKey(ServiceVersion,on_delete=PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["direction","script"],name="unique_brand_typography")]
class BrandAsset(Entity):
    direction = models.ForeignKey(BrandDirection,on_delete=PROTECT)
    artifact = models.ForeignKey(Artifact,on_delete=PROTECT)
    kind = models.CharField(max_length=50)
    usage_rule = models.TextField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=["direction","artifact","kind"],name="unique_brand_asset")]
class FailureRecord(Entity):
    key = models.CharField(max_length=100,unique=True)
    task = models.ForeignKey(RegistryTask,on_delete=PROTECT)
    session = models.ForeignKey(ExecutionSession,on_delete=PROTECT)
    symptom = models.TextField()
    cause = models.TextField()
    correction = models.TextField()
    prevention = models.TextField()
    evidence = models.TextField()
class RegistryEvent(Entity):
    key = models.CharField(max_length=150,unique=True)
    session = models.ForeignKey(ExecutionSession,on_delete=PROTECT)
    action = models.CharField(max_length=100)
    summary = models.JSONField(default=dict)
