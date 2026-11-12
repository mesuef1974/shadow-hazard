import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from competition import training
from competition.models import (
    TrainingCourse,
    TrainingLesson,
    TrainingRecording,
    TrainingSlide,
    TranscriptSegment,
)


class TrainingPagesTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = override_settings(LOCAL_DIR=Path(self.temp.name))
        config.enable()
        self.addCleanup(config.disable)
        users = get_user_model().objects
        self.owner = users.create_user(username="mesuef", role="supervisor")
        self.student = users.create_user(username="saud", role="student")
        now = datetime(2026, 11, 12, 12, 30, tzinfo=UTC)
        course = TrainingCourse.objects.create(
            key="c", kind="mandatory", title="التدريب الأساسي", source_url="https://x/c",
            scraped_at=now)
        self.first = TrainingLesson.objects.create(
            course=course, key="a", position=1, title="الدرس الأول", source_url="https://x/a",
            outline=["المقدمة"])
        self.second = TrainingLesson.objects.create(
            course=course, key="b", position=2, title="الدرس الثاني", source_url="https://x/b")
        training.slides_dir().mkdir(parents=True)
        (training.slides_dir() / "aa.png").write_bytes(b"png-bytes")
        self.slide = TrainingSlide.objects.create(
            lesson=self.first, position=1, image_path="aa.png", image_sha256="aa",
            image_url="https://firebasestorage.googleapis.com/x?token=SECRET",
            media_urls=["https://v/x?token=SECRET"], ocr_text="حساس المسافة\nيقيس البعد",
            ocr_at=now)
        self.bare = TrainingSlide.objects.create(lesson=self.first, position=2, text="نص أصلي")
        self.recording = TrainingRecording.objects.create(
            key="r", title="اللقاء التعريفي", duration_seconds=3723, imported_at=now)
        TranscriptSegment.objects.create(
            recording=self.recording, position=1, start_seconds=3723, text="مرحبا بالفرق")
        self.pages = ["/training/", f"/training/lessons/{self.first.pk}/",
                      f"/training/recordings/{self.recording.pk}/",
                      f"/training/slides/{self.slide.pk}/image"]

    def status(self, page):
        response = self.client.get(page)
        response.close()  # يغلق ملف الصورة المفتوح للبث؛ ويندوز لا يحذف ملفًا مفتوحًا
        return response.status_code

    def test_pages_are_for_the_supervisor_only(self):
        for page in self.pages:
            self.assertEqual(self.status(page), 302, page)  # إلى الدخول
        self.client.force_login(self.student)
        for page in self.pages:
            self.assertEqual(self.status(page), 403, page)
        self.client.force_login(self.owner)
        for page in self.pages:
            self.assertEqual(self.status(page), 200, page)

    def test_menu_link_shows_for_supervisor_only(self):
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get("/"), "/training/")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/"), 'href="/training/"')

    def test_index_lists_courses_lessons_and_recordings(self):
        self.client.force_login(self.owner)
        page = self.client.get("/training/")
        self.assertContains(page, "التدريب الأساسي")
        self.assertContains(page, f"/training/lessons/{self.second.pk}/")
        self.assertContains(page, "2 شريحة")
        self.assertContains(page, "تفريغ آلي غير مراجَع")
        self.assertIsNone(page.context["results"])

    def test_search_finds_slides_and_segments_with_anchors(self):
        self.client.force_login(self.owner)
        page = self.client.get("/training/", {"q": "المسافة"})
        self.assertContains(page, f"/training/lessons/{self.first.pk}/#slide-1")
        self.assertEqual(page.context["results"]["segment_total"], 0)
        page = self.client.get("/training/", {"q": "بالفرق"})
        self.assertContains(page, f"/training/recordings/{self.recording.pk}/#segment-1")
        page = self.client.get("/training/", {"q": "لا-يوجد"})
        self.assertContains(page, "لا نتائج.")
        self.assertIsNone(self.client.get("/training/", {"q": "   "}).context["results"])

    def test_lesson_shows_slides_in_order_without_source_links(self):
        self.client.force_login(self.owner)
        page = self.client.get(f"/training/lessons/{self.first.pk}/")
        self.assertEqual([slide.position for slide in page.context["slides"]], [1, 2])
        self.assertContains(page, f"/training/slides/{self.slide.pk}/image")
        self.assertContains(page, "حساس المسافة")
        self.assertContains(page, "نص أصلي")
        self.assertContains(page, "لا صورة لهذه الشريحة.")
        self.assertNotContains(page, "SECRET")  # روابط المصدر تحمل رمز وصول
        self.assertIsNone(page.context["previous"])
        self.assertEqual(page.context["next"], self.second)
        self.assertEqual(self.client.get("/training/lessons/999999/").status_code, 404)

    def test_recording_shows_clock_and_unreviewed_warning(self):
        self.client.force_login(self.owner)
        page = self.client.get(f"/training/recordings/{self.recording.pk}/")
        self.assertContains(page, "1:02:03")
        self.assertContains(page, "مرحبا بالفرق")
        self.assertContains(page, "تفريغ آلي غير مراجَع")

    def test_slide_image_serves_only_files_inside_the_slides_folder(self):
        self.client.force_login(self.owner)
        image = self.client.get(f"/training/slides/{self.slide.pk}/image")
        self.assertEqual(b"".join(image.streaming_content), b"png-bytes")
        self.assertEqual(image["Content-Type"], "image/png")
        self.assertIn("private", image["Cache-Control"])
        self.assertEqual(self.client.get(f"/training/slides/{self.bare.pk}/image").status_code, 404)
        outside = Path(self.temp.name) / "secret.png"
        outside.write_bytes(b"outside")
        TrainingSlide.objects.filter(pk=self.slide.pk).update(image_path="../../secret.png")
        self.assertEqual(
            self.client.get(f"/training/slides/{self.slide.pk}/image").status_code, 404)

    def test_snippet_centers_on_the_match(self):
        text = "ا" * 200 + " الكلمة " + "ب" * 200
        cut = training.snippet(text, "الكلمة", radius=10)
        self.assertIn("الكلمة", cut)
        self.assertTrue(cut.startswith("…") and cut.endswith("…"))
        self.assertLess(len(cut), 40)
        self.assertEqual(training.snippet("قصير", "غائب"), "قصير")
        self.assertEqual(training.clock(59), "0:00:59")


DIGEST = {
    "headline": "لقاء تعريفي بأربعة مشاريع",
    "overview": "نظرة عامة",
    "takeaways": [{"title": "التدريب اختياري", "text": "ليس جزءًا من التحكيم", "at": 65}],
    "chapters": [
        {"title": "الترحيب", "start": 0, "end": 60, "points": ["تقديم المدربين", " "]},
        {"title": "الأسئلة", "start": 60, "end": 120, "points": ["سؤال المضخة"]},
    ],
    "facts": [{"kind": "موعد", "label": "اليوم الثاني", "value": "21 نوفمبر", "at": 15},
              {"kind": "موعد", "label": "اليوم الثالث", "value": "19 ديسمبر", "at": None}],
    "actions": [{"text": "قراءة دليل القواعد", "at": 100}],
    "questions": [{"question": "هل يحاكي؟", "answer": "لا", "at": 110}],
    "doubts": [{"text": "اسم الشركة غير مؤكد", "at": 5}],
}


class RecordingDigestTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="mesuef", role="supervisor")
        self.recording = TrainingRecording.objects.create(
            key="r", title="اللقاء الأول", duration_seconds=120,
            imported_at=datetime(2026, 11, 12, 12, 30, tzinfo=UTC))
        TranscriptSegment.objects.bulk_create([
            TranscriptSegment(recording=self.recording, position=i, start_seconds=start, text="نص")
            for i, start in enumerate((0, 50, 100), 1)])
        self.url = f"/training/recordings/{self.recording.pk}/"
        self.client.force_login(self.owner)

    def test_page_without_digest_opens_raw_transcript(self):
        page = self.client.get(self.url)
        self.assertIsNone(page.context["digest"])
        self.assertContains(page, "لا ملخص لهذا اللقاء بعد")
        self.assertContains(page, 'id="transcript" open')

    def test_digest_import_cleans_and_page_links_times_to_segments(self):
        training.import_digest("r", DIGEST)
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.digest["chapters"][0]["points"], ["تقديم المدربين"])
        self.assertIsNotNone(self.recording.digest_at)
        page = self.client.get(self.url)
        digest = page.context["digest"]
        # 65 ثانية تقع في المقطع الثاني (يبدأ عند 50)، و100 في الثالث
        self.assertEqual(digest["takeaways"][0]["mark"], {"clock": "0:01:05", "segment": 2})
        self.assertEqual(digest["actions"][0]["mark"]["segment"], 3)
        self.assertIsNone(digest["facts"][1]["mark"])
        self.assertEqual([c["share"] for c in digest["chapters"]], [50.0, 50.0])
        self.assertEqual([kind for kind, _ in digest["fact_groups"]], ["موعد"])
        self.assertContains(page, "لقاء تعريفي بأربعة مشاريع")
        self.assertContains(page, 'href="#segment-2"')
        self.assertContains(page, 'href="#chapter-2"')
        self.assertNotContains(page, 'id="transcript" open')

    def test_digest_rejects_bad_shapes(self):
        for bad in ([], {"overview": "بلا عنوان"}, {**DIGEST, "extra": 1},
                    {**DIGEST, "actions": "نص"}, {**DIGEST, "actions": [{"text": "x", "at": -1}]},
                    {**DIGEST, "actions": [{"text": "x", "at": "12"}]},
                    {**DIGEST, "actions": [{"text": "x", "who": "y"}]},
                    {**DIGEST, "chapters": [{"title": "x", "start": 0, "end": 1, "points": "p"}]}):
            with self.assertRaises(ValueError):
                training.clean_digest(bad)

    def test_digest_command_imports_file_and_reports_counts(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "digest.json"
            path.write_text(json.dumps(DIGEST, ensure_ascii=False), encoding="utf-8")
            out = StringIO()
            call_command("training_content", "digest", str(path), "--key", "r", stdout=out)
            self.assertEqual(json.loads(out.getvalue())["chapters"], 2)
            with self.assertRaises(CommandError):
                call_command("training_content", "digest", str(path), "--key", "missing")
