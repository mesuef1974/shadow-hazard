import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from competition import training
from competition.models import (
    TrainingCourse,
    TrainingLesson,
    TrainingRecording,
    TrainingSlide,
    TranscriptSegment,
)

NOW = datetime(2026, 11, 12, 12, 30, tzinfo=UTC)


def course_file():
    return {
        "course": {"key": "c", "headline": "أساسيات إنترنت الأشياء", "overview": "نظرة",
                   "project": ["مخطط العناصر الخمسة قائمة مراجعة للعتاد", " "]},
        "lessons": [
            {"key": "a", "headline": "الحساس يقيس والمتحكم يقرّر", "overview": "ملخص",
             "points": [{"text": "الريليه مفتاح وسيط", "slide": 2}],
             "terms": [{"term": "Relay", "meaning": "مفتاح يُتحكم به كهربائيًا"}],
             "project": [{"text": "نشغّل اللوحة الخلفية عبر ريليه", "slide": 2}],
             "doubts": [{"text": "رقم الجهد غير واضح", "slide": None}]},
            {"key": "b", "headline": "درس بلا فائدة للمشروع", "overview": "",
             "points": [], "terms": [], "project": [], "doubts": []},
        ],
    }


def playbook_file():
    return {
        "headline": "نأخذ من التدريب أدوات التحذير لا نواة الظل",
        "overview": "نظرة",
        "areas": [{"title": "مخارج التحذير", "why": "ثلاثة مخارج", "steps": [
            {"text": "ريليه للّوحة الخلفية", "sources": [
                {"lesson": "a", "slide": 2}, {"recording": "r", "at": 65, "label": "اللقاء"},
                {"lesson": "missing"}]},
            {"text": "خطوة بلا مصدر", "sources": []}]}],
        "gaps": ["معايرة الكاميرا"],
    }


class LessonDigestTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="mesuef", role="supervisor")
        self.course = TrainingCourse.objects.create(
            key="c", kind="course", title="إنترنت الأشياء", source_url="https://x/c",
            scraped_at=NOW)
        self.first = TrainingLesson.objects.create(
            course=self.course, key="a", position=1, title="الدرس الأول", source_url="https://x/a")
        self.second = TrainingLesson.objects.create(
            course=self.course, key="b", position=2, title="الدرس الثاني", source_url="https://x/b")
        TrainingSlide.objects.create(lesson=self.first, position=2, text="نص")
        self.recording = TrainingRecording.objects.create(
            key="r", title="اللقاء الأول", duration_seconds=120, imported_at=NOW)
        TranscriptSegment.objects.bulk_create([
            TranscriptSegment(recording=self.recording, position=i, start_seconds=start, text="نص")
            for i, start in enumerate((0, 50, 100), 1)])
        self.client.force_login(self.owner)

    def test_import_saves_course_and_lesson_digests_and_reports_missing(self):
        data = course_file()
        data["lessons"].pop()
        result = training.import_lesson_digests(data)
        self.assertEqual(result, {"course": "c", "lessons": 1, "missing": 1})
        self.course.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual(self.course.digest["project"], ["مخطط العناصر الخمسة قائمة مراجعة للعتاد"])
        self.assertEqual(self.first.digest["points"], [{"text": "الريليه مفتاح وسيط", "slide": 2}])
        self.assertIsNone(self.first.digest["doubts"][0]["slide"])

    def test_import_rejects_unknown_keys_and_leaves_nothing_half_written(self):
        bad = [
            {"course": {"key": "zz", "headline": "x"}, "lessons": []},
            {**course_file(), "extra": 1},
            {"course": course_file()["course"], "lessons": [{"key": "nope", "headline": "x"}]},
            {"course": course_file()["course"],
             "lessons": [course_file()["lessons"][0], course_file()["lessons"][0]]},
            {"course": course_file()["course"],
             "lessons": [{"key": "a", "headline": "x", "points": [{"text": "t", "slide": "2"}]}]},
        ]
        for data in bad:
            with self.assertRaises(ValueError):
                training.import_lesson_digests(data)
        self.course.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual((self.course.digest, self.first.digest), ({}, {}))

    def test_lesson_page_shows_digest_with_slide_links(self):
        page = self.client.get(f"/training/lessons/{self.first.pk}/")
        self.assertIsNone(page.context["digest"])
        self.assertNotContains(page, "ما يفيد ظل الخطر")
        training.import_lesson_digests(course_file())
        page = self.client.get(f"/training/lessons/{self.first.pk}/")
        self.assertContains(page, "الحساس يقيس والمتحكم يقرّر")
        self.assertContains(page, "نشغّل اللوحة الخلفية عبر ريليه")
        self.assertContains(page, 'href="#slide-2"')
        self.assertContains(page, "Relay")
        page = self.client.get(f"/training/lessons/{self.second.pk}/")
        self.assertNotContains(page, "ما يفيد ظل الخطر")  # قائمته فارغة فلا يُعرض القسم

    def test_index_shows_headlines_and_project_link(self):
        training.import_lesson_digests(course_file())
        page = self.client.get("/training/")
        self.assertContains(page, "أساسيات إنترنت الأشياء")
        self.assertContains(page, "الحساس يقيس والمتحكم يقرّر")
        self.assertContains(page, 'href="/training/project/"')

    def test_project_page_groups_items_by_source(self):
        page = self.client.get("/training/project/")
        self.assertEqual(page.context["groups"], [])
        self.assertContains(page, "لا بنود بعد")
        training.import_lesson_digests(course_file())
        training.import_digest(
            "r", {"headline": "ع", "project": [{"text": "نقيس العتبات", "at": 65}]})
        page = self.client.get("/training/project/")
        meeting, course = page.context["groups"]
        self.assertEqual((meeting["kind"], meeting["count"]), ("لقاء", 1))
        self.assertEqual(
            meeting["rows"][0]["url"], f"/training/recordings/{self.recording.pk}/#segment-2")
        self.assertEqual(course["count"], 2)
        self.assertEqual(course["rows"][0]["url"], f"/training/lessons/{self.first.pk}/#slide-2")
        self.assertEqual(page.context["item_count"], 3)
        self.assertContains(page, "لم تُكتب الخلاصة المصاغة بعد")

    def test_playbook_resolves_sources_to_links(self):
        training.import_playbook(playbook_file(), now=NOW)
        page = self.client.get("/training/project/")
        sources = page.context["playbook"]["areas"][0]["steps"][0]["sources"]
        self.assertEqual(sources[0], {
            "label": "الدرس الأول", "kind": "lesson", "detail": "الشريحة 2",
            "url": f"/training/lessons/{self.first.pk}/#slide-2"})
        self.assertEqual((sources[1]["url"], sources[1]["detail"], sources[1]["label"]), (
            f"/training/recordings/{self.recording.pk}/#segment-2", "0:01:05", "اللقاء"))
        self.assertEqual((sources[2]["kind"], sources[2]["url"]), ("missing", ""))
        self.assertContains(page, "نأخذ من التدريب أدوات التحذير لا نواة الظل")
        self.assertContains(page, "معايرة الكاميرا")
        self.assertContains(page, 'href="#area-1"')

    def test_playbook_rejects_bad_shapes(self):
        base = playbook_file()
        for bad in ({**base, "areas": []}, {**base, "extra": 1}, {**base, "gaps": [1]},
                    {**base, "areas": [{"title": "x", "steps": [{"text": "t", "sources": [{}]}]}]},
                    {**base, "areas": [{"title": "x", "steps": [
                        {"text": "t", "sources": [{"lesson": "a", "recording": "r"}]}]}]},
                    {**base, "areas": [{"title": "x", "steps": [
                        {"text": "t", "sources": [{"lesson": "a", "slide": "2"}]}]}]}):
            with self.assertRaises(ValueError):
                training.clean_playbook(bad)

    def test_commands_import_files(self):
        with TemporaryDirectory() as folder:
            lessons, playbook = Path(folder) / "c.json", Path(folder) / "p.json"
            lessons.write_text(json.dumps(course_file(), ensure_ascii=False), encoding="utf-8")
            playbook.write_text(json.dumps(playbook_file(), ensure_ascii=False), encoding="utf-8")
            out = StringIO()
            call_command("training_content", "lesson-digests", str(lessons), stdout=out)
            call_command("training_content", "playbook", str(playbook), stdout=out)
            first, second = (json.loads(line) for line in out.getvalue().splitlines())
            self.assertEqual((first["lessons"], second), (2, {"areas": 1, "steps": 2, "gaps": 1}))
            lessons.write_text("{}", encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("training_content", "lesson-digests", str(lessons))

    def test_student_cannot_open_project_page(self):
        student = get_user_model().objects.create_user(username="saud", role="student")
        self.client.force_login(student)
        self.assertEqual(self.client.get("/training/project/").status_code, 403)
