from django.contrib import admin

from .models import (
    CompetitionFact,
    GoldenProject,
    PastTeam,
    RubricCriterion,
    TrainingCourse,
    TrainingLesson,
    TrainingRecording,
    TrainingSlide,
    TranscriptSegment,
)

for model in (CompetitionFact, GoldenProject, PastTeam, RubricCriterion):
    admin.site.register(model)


@admin.register(TrainingCourse)
class TrainingCourseAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "position", "scraped_at")


@admin.register(TrainingLesson)
class TrainingLessonAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "position", "duration_label")
    list_filter = ("course",)
    search_fields = ("title",)


@admin.register(TrainingSlide)
class TrainingSlideAdmin(admin.ModelAdmin):
    list_display = ("lesson", "position", "image_bytes", "ocr_engine", "ocr_at")
    list_filter = ("lesson__course",)
    search_fields = ("ocr_text", "text")
    exclude = ("image_url", "media_urls")  # روابط تحمل رمز وصول


@admin.register(TrainingRecording)
class TrainingRecordingAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "held_on", "duration_seconds", "reviewed")


@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(admin.ModelAdmin):
    list_display = ("recording", "position", "start_seconds")
    search_fields = ("text",)
