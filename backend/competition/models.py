from django.db import models


class CompetitionFact(models.Model):
    """حقيقة منقولة من موقع المسابقة أو دليل القواعد، مع مصدرها."""
    season = models.PositiveSmallIntegerField(default=6)
    section = models.CharField(max_length=60)
    key = models.CharField(max_length=120)
    value = models.TextField()
    source = models.CharField(max_length=300)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "section", "key"], name="competition_fact_unique")]
        ordering = ["season", "section", "id"]


class RubricCriterion(models.Model):
    """معيار تقييم رئيسي لفئة (junior/senior) من 100 درجة."""
    category = models.CharField(max_length=10)
    position = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=160)
    max_score = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["category", "position"], name="competition_rubric_unique")]
        ordering = ["category", "position"]


class GoldenProject(models.Model):
    number = models.PositiveSmallIntegerField(unique=True)
    name_ar = models.CharField(max_length=160)
    name_en = models.CharField(max_length=160)
    core_challenge = models.TextField()

    class Meta:
        ordering = ["number"]


class PastTeam(models.Model):
    """فريق معروض في معرض المشاريع لموسم سابق (المعرض لا يذكر المراتب)."""
    season_no = models.PositiveSmallIntegerField()
    season_year = models.PositiveSmallIntegerField()
    team_name = models.CharField(max_length=160)
    video_id = models.CharField(max_length=20, blank=True)
    video_title = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season_no", "team_name"], name="competition_pastteam_unique")]
        ordering = ["season_no", "id"]


class TrainingCourse(models.Model):
    """دورة من صفحة المحتوى التدريبي في منصة المسابقة؛ المفتاح معرّفها في رابطها."""
    key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=20, choices=[("mandatory", "إلزامي"), ("course", "دورة")])
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    source_url = models.URLField(max_length=300)
    position = models.PositiveSmallIntegerField(default=0)
    scraped_at = models.DateTimeField()

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.title


class TrainingLesson(models.Model):
    course = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE, related_name="lessons")
    key = models.CharField(max_length=64)
    position = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=300)
    duration_label = models.CharField(max_length=60, blank=True)
    outline = models.JSONField(default=list, blank=True)
    source_url = models.URLField(max_length=300)

    class Meta:
        ordering = ["course_id", "position"]
        constraints = [
            models.UniqueConstraint(fields=["course", "key"], name="training_lesson_unique"),
        ]

    def __str__(self):
        return self.title


class TrainingSlide(models.Model):
    """شريحة درس: صورتها منزَّلة محليًا (خارج المستودع) ونصها مقروء ضوئيًا."""
    lesson = models.ForeignKey(TrainingLesson, on_delete=models.CASCADE, related_name="slides")
    position = models.PositiveSmallIntegerField()
    # رابط الصورة يحمل رمز وصول المنصة؛ يبقى في القاعدة المحلية ولا يُعرض لغير المشرف.
    image_url = models.TextField(blank=True)
    media_urls = models.JSONField(default=list, blank=True)
    text = models.TextField(blank=True)
    image_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    image_path = models.CharField(max_length=300, blank=True)
    image_bytes = models.PositiveIntegerField(default=0)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    ocr_text = models.TextField(blank=True)
    ocr_engine = models.CharField(max_length=80, blank=True)
    ocr_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["lesson_id", "position"]
        constraints = [
            models.UniqueConstraint(fields=["lesson", "position"], name="training_slide_unique"),
        ]

    def __str__(self):
        return f"{self.lesson} — {self.position}"


class TrainingRecording(models.Model):
    """تسجيل لقاء تدريبي وتفريغه الآلي؛ reviewed يبقى False حتى يراجعه إنسان."""
    key = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=40, blank=True)
    held_on = models.DateField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    engine = models.CharField(max_length=120, blank=True)
    source_digest = models.CharField(max_length=64, blank=True)
    reviewed = models.BooleanField(default=False)
    imported_at = models.DateTimeField()

    def __str__(self):
        return self.title


class TranscriptSegment(models.Model):
    recording = models.ForeignKey(
        TrainingRecording, on_delete=models.CASCADE, related_name="segments")
    position = models.PositiveIntegerField()
    start_seconds = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        ordering = ["recording_id", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["recording", "position"], name="training_segment_unique"),
        ]
