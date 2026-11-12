"""صفحات عرض المحتوى التدريبي للمشرف: الدورات، الدرس بشرائحه ونصوصها، وتفريغ اللقاء."""
import mimetypes
from functools import wraps
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render

from . import training
from .models import TrainingCourse, TrainingLesson, TrainingRecording, TrainingSlide

FORBIDDEN = "المحتوى التدريبي للمشرف"
QUERY_LIMIT = 100


def supervisor_only(view):
    @login_required(login_url="/login/")
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if getattr(request.user, "role", None) != "supervisor":
            return HttpResponseForbidden(FORBIDDEN)
        return view(request, *args, **kwargs)
    return guarded


@supervisor_only
def index(request):
    query = (request.GET.get("q") or "").strip()[:QUERY_LIMIT]
    courses = TrainingCourse.objects.prefetch_related(
        training.lessons_with_slide_counts())
    context = {
        "courses": courses,
        "recordings": TrainingRecording.objects.annotate(segment_count=Count("segments")),
        "totals": training.totals(),
        "query": query,
        "results": training.search(query) if query else None,
    }
    return render(request, "competition/training_index.html", context)


@supervisor_only
def project(request):
    """كيف يفيد المحتوى التدريبي المشروع: الخلاصة المصاغة ثم كل البنود بمصادرها."""
    groups = training.project_items()
    context = {"playbook": training.playbook_view(), "groups": groups,
               "item_count": sum(group["count"] for group in groups)}
    return render(request, "competition/training_project.html", context)


@supervisor_only
def lesson(request, pk):
    item = get_object_or_404(TrainingLesson.objects.select_related("course"), pk=pk)
    siblings = TrainingLesson.objects.filter(course=item.course)
    context = {
        "lesson": item,
        "digest": training.lesson_digest_view(item),
        "slides": item.slides.all(),
        "previous": siblings.filter(position__lt=item.position).order_by("-position").first(),
        "next": siblings.filter(position__gt=item.position).order_by("position").first(),
    }
    return render(request, "competition/training_lesson.html", context)


@supervisor_only
def recording(request, pk):
    item = get_object_or_404(TrainingRecording, pk=pk)
    segments = [
        {"clock": training.clock(start), "position": position, "text": text}
        for position, start, text in item.segments.values_list("position", "start_seconds", "text")
    ]
    context = {"recording": item, "segments": segments, "digest": training.digest_view(item),
               "duration": training.clock(item.duration_seconds)}
    return render(request, "competition/training_recording.html", context)


@supervisor_only
def slide_image(request, pk):
    """صورة الشريحة من مجلد العمل المحلي؛ الاسم من القاعدة ويُقصَر على اسم ملف داخل المجلد."""
    slide = get_object_or_404(TrainingSlide, pk=pk)
    if not slide.image_path:
        raise Http404
    path = training.slides_dir() / Path(slide.image_path).name
    if not path.is_file():
        raise Http404
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    response = FileResponse(path.open("rb"), content_type=kind)
    # الملف مسمّى ببصمة محتواه فلا يتغير؛ الذاكرة خاصة بمتصفح المشرف.
    response["Cache-Control"] = "private, max-age=86400, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    return response
