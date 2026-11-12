"""أمر واحد لمراحل المحتوى التدريبي؛ كل مرحلة آمنة للتكرار وتكمل من حيث توقفت.

    manage.py training_content crawl <ملف JSON من tools/training_crawler.js>
    manage.py training_content slides [--limit N]
    manage.py training_content ocr --python <بايثون فيه paddleocr> [--redo]
    manage.py training_content transcript <ملف> --key K --title T [--category C --held-on D]
    manage.py training_content status
"""
import json
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from competition import training
from competition.models import (
    TrainingCourse,
    TrainingLesson,
    TrainingRecording,
    TrainingSlide,
    TranscriptSegment,
)

OCR_TOOL = Path(settings.BASE_DIR).parent / "tools" / "training_ocr.py"


class Command(BaseCommand):
    help = "استيراد المحتوى التدريبي وتنزيل شرائحه وقراءتها ضوئيًا واستيراد تفريغ اللقاءات."

    def add_arguments(self, parser):
        stages = parser.add_subparsers(dest="stage", required=True)
        crawl = stages.add_parser("crawl")
        crawl.add_argument("path")
        slides = stages.add_parser("slides")
        slides.add_argument("--limit", type=int)
        ocr = stages.add_parser("ocr")
        ocr.add_argument("--python", default=sys.executable)
        ocr.add_argument("--lang", default="ar")
        ocr.add_argument("--redo", action="store_true")
        transcript = stages.add_parser("transcript")
        transcript.add_argument("path")
        transcript.add_argument("--key", required=True)
        transcript.add_argument("--title", required=True)
        transcript.add_argument("--category", default="")
        transcript.add_argument("--held-on")
        transcript.add_argument("--engine", default="")
        stages.add_parser("status")

    def handle(self, *args, **options):
        getattr(self, "_" + options["stage"])(options)

    def _crawl(self, options):
        data = json.loads(Path(options["path"]).read_text(encoding="utf-8-sig"))
        self.stdout.write(json.dumps(training.import_crawl(data)))

    def _slides(self, options):
        result = training.download_slides(limit=options["limit"], log=self.stderr.write)
        self.stdout.write(json.dumps(result))

    def _ocr(self, options):
        work = Path(settings.LOCAL_DIR) / "training"
        work.mkdir(parents=True, exist_ok=True)
        manifest, results = work / "ocr-manifest.jsonl", work / "ocr-results.jsonl"
        rows = training.ocr_manifest(redo=options["redo"])
        manifest.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        if options["redo"]:
            results.unlink(missing_ok=True)
        if rows:
            # المحرك في بايثون مستقل (فيه paddleocr) فلا تُثقَّل بيئة المنصة؛ بلا نافذة ظاهرة.
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            done = subprocess.run(
                [options["python"], str(OCR_TOOL), str(manifest), str(results),
                 "--lang", options["lang"]],
                stdin=subprocess.DEVNULL, creationflags=flags, check=False)
            if done.returncode != 0:
                raise CommandError(f"OCR tool exited with {done.returncode}")
        updated = 0
        if results.exists():
            with results.open(encoding="utf-8") as stream:
                updated = training.import_ocr(stream)
        self.stdout.write(json.dumps({"images": len(rows), "slides_updated": updated}))

    def _transcript(self, options):
        _, segments = training.import_transcript(
            options["path"], options["key"], options["title"], options["category"],
            options["held_on"], options["engine"])
        self.stdout.write(json.dumps({"segments": segments}))

    def _status(self, options):
        slides = TrainingSlide.objects
        self.stdout.write(json.dumps({
            "courses": TrainingCourse.objects.count(),
            "lessons": TrainingLesson.objects.count(),
            "lessons_without_slides": TrainingLesson.objects.filter(slides__isnull=True).count(),
            "slides": slides.count(),
            "slides_downloaded": slides.exclude(image_sha256="").count(),
            "slides_read": slides.filter(ocr_at__isnull=False).count(),
            "slides_read_empty": slides.filter(ocr_at__isnull=False, ocr_text="").count(),
            "recordings": TrainingRecording.objects.count(),
            "segments": TranscriptSegment.objects.count(),
        }))
