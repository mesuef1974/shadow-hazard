"""خدمات المحتوى التدريبي: استيراد ناتج النسخ، تنزيل الشرائح، نتائج القراءة الضوئية، التفريغ.

كل دالة آمنة للتكرار (idempotent): تُحدَّث السجلات بمفاتيحها ولا تتكرر.
"""
import hashlib
import json
import re
from bisect import bisect_right
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from .models import (
    TrainingCourse,
    TrainingLesson,
    TrainingRecording,
    TrainingSlide,
    TranscriptSegment,
)

SITE = "https://hamdanfsc.com"
# المصدر الوحيد المسموح لتنزيل الصور (حماية من طلبات إلى عناوين غير مقصودة).
ALLOWED_IMAGE_HOSTS = frozenset({"firebasestorage.googleapis.com"})
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ICONS = re.compile(r"\b(?:arrow_back|arrow_forward|school|verified|menu_book|schedule|translate)\b")
NOISE = frozenset({
    "المحتوى التدريبي", "إلزامي", "دورة تدريبية", "جميع المواد التدريبية باللغة العربية"})
LESSON_LABEL = re.compile(r"^الدرس\s+(\d+)\s+(.*)$")
DURATION = re.compile(r"\s+(\d+\s+دقيقة|ساعة(?:\s+و\d+\s+دقيقة)?|ساعتان|\d+\s+ساعات)$")
STAMP = re.compile(r"^\[(\d+):(\d\d):(\d\d)\]\s*(.*)$")


def slides_dir():
    return Path(settings.LOCAL_DIR) / "training" / "slides"


def _course_fields(url, head):
    parts = [p.strip() for p in ICONS.sub("", head).split("|")]
    parts = [p for p in parts if p and p not in NOISE]
    return (parts[0] if parts else url), " | ".join(parts[1:])


def _lesson_fields(label):
    match = LESSON_LABEL.match(label.strip())
    rest = match.group(2).strip() if match else label.strip()
    duration = DURATION.search(rest)
    if duration:
        return rest[:duration.start()].strip(), duration.group(1)
    return rest, ""


def _stable(url):
    """الرابط بلا رمز الوصول المتغيّر؛ به يُعرف هل تبدّلت الصورة."""
    return url.split("?", 1)[0]


def _last(url):
    return url.rstrip("/").rsplit("/", 1)[-1]


@transaction.atomic
def import_crawl(courses, now=None):
    """يستورد ناتج tools/training_crawler.js. يعيد أعداد الدورات والدروس والشرائح."""
    now = now or timezone.now()
    counts = {"courses": 0, "lessons": 0, "slides": 0}
    for course_position, item in enumerate(courses, 1):
        url = item["url"]
        title, summary = _course_fields(url, item.get("head", ""))
        course, _ = TrainingCourse.objects.update_or_create(key=_last(url), defaults={
            "kind": "mandatory" if "/mandatory/" in url else "course", "title": title[:200],
            "summary": summary, "source_url": SITE + url, "position": course_position,
            "scraped_at": now,
        })
        counts["courses"] += 1
        for lesson_position, row in enumerate(item.get("lessons", []), 1):
            lesson_title, duration = _lesson_fields(row.get("label", ""))
            lesson, _ = TrainingLesson.objects.update_or_create(
                course=course, key=_last(row["href"]), defaults={
                    "position": lesson_position, "title": lesson_title[:300],
                    "duration_label": duration, "outline": row.get("outline", []),
                    "source_url": SITE + row["href"],
                })
            counts["lessons"] += 1
            steps = row.get("steps", [])
            for slide_position, step in enumerate(steps, 1):
                _save_slide(lesson, slide_position, step)
                counts["slides"] += 1
            if steps:  # درس فشل نسخه (بلا شرائح) لا يمحو شرائح نسخة سابقة.
                lesson.slides.filter(position__gt=len(steps)).delete()
    return counts


def _save_slide(lesson, position, step):
    images = step.get("img") or [""]
    slide, created = TrainingSlide.objects.get_or_create(
        lesson=lesson, position=position, defaults={"image_url": images[0]})
    # تغيّر الرابط (بلا رمزه) يعني صورة جديدة: يُصفَّر ما نُزِّل وقُرئ.
    if not created and _stable(slide.image_url) != _stable(images[0]):
        slide.image_sha256 = slide.image_path = slide.ocr_text = slide.ocr_engine = ""
        slide.image_bytes, slide.downloaded_at, slide.ocr_at = 0, None, None
    slide.image_url = images[0]
    slide.media_urls = list(images[1:]) + list(step.get("med", []))
    slide.text = step.get("text", "")
    slide.save()


def _read_image(url, timeout=60):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_IMAGE_HOSTS:
        raise ValueError("image host not allowed")
    request = Request(url, headers={"User-Agent": "shadow-hazard-training/1"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - المضيف مقيَّد أعلاه
        if not response.headers.get_content_type().startswith("image/"):
            raise ValueError("not an image")
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image too large")
    return data


def _pending_download():
    return TrainingSlide.objects.filter(image_sha256="").exclude(image_url="")


def download_slides(read=_read_image, limit=None, log=lambda message: None):
    """ينزّل صور الشرائح التي لم تُنزَّل بعد. الملف يُسمّى بتجزئته فلا تتكرر الصور."""
    target = slides_dir()
    target.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    for slide in _pending_download().iterator():
        if limit is not None and done >= limit:
            break
        try:
            data = read(slide.image_url)
        except Exception as exc:  # فشل صورة لا يوقف الباقي؛ إعادة التشغيل تكمل.
            failed += 1
            log(f"failed slide {slide.pk}: {type(exc).__name__}")
            continue
        sha = hashlib.sha256(data).hexdigest()
        suffix = Path(urlsplit(slide.image_url).path).suffix.lower()
        name = sha + (suffix if suffix in IMAGE_SUFFIXES else ".img")
        if not (target / name).exists():
            (target / name).write_bytes(data)
        slide.image_sha256, slide.image_path, slide.image_bytes = sha, name, len(data)
        slide.downloaded_at = timezone.now()
        slide.save(update_fields=["image_sha256", "image_path", "image_bytes", "downloaded_at"])
        done += 1
    return {"downloaded": done, "failed": failed, "remaining": _pending_download().count()}


def ocr_manifest(redo=False):
    """صور تنتظر القراءة: سطر لكل تجزئة فريدة (الصورة المكررة تُقرأ مرة واحدة)."""
    slides = TrainingSlide.objects.exclude(image_sha256="")
    if not redo:
        slides = slides.filter(ocr_at__isnull=True)
    names = dict(slides.values_list("image_sha256", "image_path"))
    folder = slides_dir()
    return [{"sha256": sha, "path": str(folder / name)} for sha, name in sorted(names.items())]


def import_ocr(lines, now=None):
    """يستورد ناتج tools/training_ocr.py (JSONL) إلى كل الشرائح التي تحمل التجزئة."""
    now = now or timezone.now()
    updated = 0
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("error"):
            continue
        updated += TrainingSlide.objects.filter(image_sha256=row["sha256"]).update(
            ocr_text=row.get("text", ""), ocr_engine=row.get("engine", "")[:80], ocr_at=now)
    return updated


@transaction.atomic
def import_transcript(path, key, title, category="", held_on=None, engine="", now=None):
    """يستورد ملف تفريغ بصيغة «[HH:MM:SS] نص». إعادة الاستيراد تستبدل المقاطع."""
    raw = Path(path).read_bytes()
    rows = []
    for line in raw.decode("utf-8-sig").splitlines():
        match = STAMP.match(line.strip())
        if match and match.group(4):
            hours, minutes, seconds, text = match.groups()
            rows.append((int(hours) * 3600 + int(minutes) * 60 + int(seconds), text))
    recording, _ = TrainingRecording.objects.update_or_create(key=key, defaults={
        "title": title, "category": category, "engine": engine,
        "held_on": date.fromisoformat(held_on) if held_on else None,
        "duration_seconds": rows[-1][0] if rows else 0,
        "source_digest": hashlib.sha256(raw).hexdigest(), "imported_at": now or timezone.now(),
    })
    recording.segments.all().delete()
    TranscriptSegment.objects.bulk_create([
        TranscriptSegment(recording=recording, position=i, start_seconds=start, text=text)
        for i, (start, text) in enumerate(rows, 1)])
    return recording, len(rows)


# ---- ملخص اللقاء ----

DIGEST_TEXTS = ("headline", "overview", "author")
# القوائم وحقول كل عنصر فيها؛ "at" و"start" و"end" ثوانٍ من بداية التسجيل.
DIGEST_LISTS = {
    "takeaways": ("title", "text", "at"),
    "chapters": ("title", "start", "end", "points"),
    "facts": ("kind", "label", "value", "at"),
    "actions": ("text", "at"),
    "questions": ("question", "answer", "at"),
    "doubts": ("text", "at"),
}
DIGEST_SECONDS = frozenset({"at", "start", "end"})


def clean_digest(data):
    """يتحقق من بنية الملخص ويعيدها منظَّفة؛ يرفض المفاتيح المجهولة والأنواع الخاطئة."""
    if not isinstance(data, dict):
        raise ValueError("digest must be an object")
    unknown = set(data) - set(DIGEST_TEXTS) - set(DIGEST_LISTS)
    if unknown:
        raise ValueError(f"unknown digest keys: {sorted(unknown)}")
    clean = {key: str(data.get(key) or "").strip() for key in DIGEST_TEXTS}
    if not clean["headline"]:
        raise ValueError("digest needs a headline")
    for name, fields in DIGEST_LISTS.items():
        rows = data.get(name) or []
        if not isinstance(rows, list):
            raise ValueError(f"{name} must be a list")
        clean[name] = [_digest_row(name, row, fields) for row in rows]
    return clean


def _digest_row(name, row, fields):
    if not isinstance(row, dict) or set(row) - set(fields):
        raise ValueError(f"bad item in {name}: {row!r}"[:200])
    item = {}
    for field in fields:
        value = row.get(field)
        if field in DIGEST_SECONDS:
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or value < 0):
                raise ValueError(f"{name}.{field} must be seconds: {value!r}")
            item[field] = value
        elif field == "points":
            if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                raise ValueError(f"{name}.points must be a list of strings")
            item[field] = [point.strip() for point in value if point.strip()]
        else:
            item[field] = str(value or "").strip()
    return item


def import_digest(key, data, now=None):
    recording = TrainingRecording.objects.get(key=key)
    recording.digest = clean_digest(data)
    recording.digest_at = now or timezone.now()
    recording.save(update_fields=["digest", "digest_at"])
    return recording


def digest_view(recording):
    """الملخص مهيَّأً للعرض: لكل توقيت ساعته ورقم مقطع التفريغ الذي يقع فيه، ولكل محور
    حصته من طول اللقاء (لخريطة اللقاء)."""
    digest = recording.digest or {}
    if not digest.get("headline"):
        return None
    starts = list(recording.segments.values_list("start_seconds", "position"))
    seconds = [start for start, _ in starts]

    def mark(value):
        if value is None:
            return None
        at = bisect_right(seconds, value) - 1
        return {"clock": clock(value), "segment": starts[max(at, 0)][1] if starts else None}

    view = {key: digest.get(key, "") for key in DIGEST_TEXTS}
    for name in DIGEST_LISTS:
        view[name] = [{**row, "mark": mark(row.get("at", row.get("start")))}
                      for row in digest.get(name, [])]
    total = max(recording.duration_seconds, 1)
    for index, chapter in enumerate(view["chapters"], 1):
        length = max((chapter.get("end") or 0) - (chapter.get("start") or 0), 0)
        chapter.update(number=index, minutes=round(length / 60),
                       share=round(max(length / total * 100, 1.5), 2))
    kinds = {}
    for fact in view["facts"]:
        kinds.setdefault(fact["kind"] or "أخرى", []).append(fact)
    view["fact_groups"] = list(kinds.items())
    return view


# ---- قراءة للعرض (صفحات المشرف) ----

SEARCH_LIMIT = 40
SNIPPET_RADIUS = 90


def clock(seconds):
    """ثوانٍ إلى ساعة:دقيقة:ثانية."""
    minutes, rest = divmod(int(seconds or 0), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}"


def lessons_with_slide_counts():
    return Prefetch("lessons", queryset=TrainingLesson.objects.annotate(
        slide_count=Count("slides")).order_by("position"))


def totals():
    slides = TrainingSlide.objects
    return {
        "courses": TrainingCourse.objects.count(),
        "lessons": TrainingLesson.objects.count(),
        "slides": slides.count(),
        "slides_read": slides.filter(ocr_at__isnull=False).count(),
        "recordings": TrainingRecording.objects.count(),
    }


def snippet(text, query, radius=SNIPPET_RADIUS):
    """مقطع من النص حول أول ظهور للعبارة؛ بدايته إن لم تظهر."""
    flat = " ".join(text.split())
    at = flat.casefold().find(query.casefold())
    start = max(0, at - radius) if at >= 0 else 0
    end = min(len(flat), (at if at >= 0 else 0) + len(query) + radius)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search(query, limit=SEARCH_LIMIT):
    """بحث نصي في نصوص الشرائح (الأصلي والمقروء ضوئيًا) وفي مقاطع التفريغ."""
    slides = (TrainingSlide.objects
              .filter(Q(ocr_text__icontains=query) | Q(text__icontains=query))
              .select_related("lesson__course").order_by("lesson__course__position",
                                                         "lesson__position", "position"))
    segments = (TranscriptSegment.objects.filter(text__icontains=query)
                .select_related("recording").order_by("recording_id", "position"))
    return {
        "slides": [{"slide": slide, "snippet": snippet(
                        slide.ocr_text if query.casefold() in slide.ocr_text.casefold()
                        else slide.text, query)}
                   for slide in slides[:limit]],
        "slide_total": slides.count(),
        "segments": [{"segment": segment, "clock": clock(segment.start_seconds),
                      "snippet": snippet(segment.text, query)}
                     for segment in segments[:limit]],
        "segment_total": segments.count(),
        "limit": limit,
    }
