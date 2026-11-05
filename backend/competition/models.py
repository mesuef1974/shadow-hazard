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
