import importlib.util
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase, override_settings

from competition import training
from competition.models import TrainingCourse, TrainingLesson, TrainingRecording, TrainingSlide

TOOLS = Path(__file__).resolve().parents[2] / "tools"
HOST = "https://firebasestorage.googleapis.com/v0/b/x/o/"


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def crawl(images=("a.jpeg", "b.JPG"), token="t1"):
    return [{
        "url": "/dawrat/mandatory/-C1",
        "head": "arrow_back | المحتوى التدريبي | school | verified | إلزامي | التدريب الأساسي"
                " | menu_book | 8 دروس | schedule | المدة الإجمالية: 6 ساعات | translate"
                " | جميع المواد التدريبية باللغة العربية | ",
        "lessons": [{
            "href": "/courseLesson/-C1/-L1", "label": "الدرس 1 مقدمة عن الذكاء الاصطناعي 45 دقيقة",
            "outline": ["المقدمة 1 / 2", "الدرس الأول"],
            "steps": [{"text": "", "img": [f"{HOST}{name}?alt=media&token={token}"], "med": []}
                      for name in images],
        }, {"href": "/courseLesson/-C1/-L2", "label": "الدرس 2 سلة المهملات الذكية ساعتان",
            "outline": [], "steps": []}],
    }]


class TrainingContentTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name)
        config = override_settings(LOCAL_DIR=self.local)
        config.enable()
        self.addCleanup(config.disable)

    def test_crawl_import_parses_titles_and_is_idempotent(self):
        for _ in range(2):
            counts = training.import_crawl(crawl())
        self.assertEqual(counts, {"courses": 1, "lessons": 2, "slides": 2})
        course = TrainingCourse.objects.get()
        self.assertEqual(
            (course.key, course.kind, course.title), ("-C1", "mandatory", "التدريب الأساسي"))
        self.assertEqual(course.summary, "8 دروس | المدة الإجمالية: 6 ساعات")
        first, second = TrainingLesson.objects.order_by("position")
        self.assertEqual(
            (first.title, first.duration_label), ("مقدمة عن الذكاء الاصطناعي", "45 دقيقة"))
        self.assertEqual((second.title, second.duration_label), ("سلة المهملات الذكية", "ساعتان"))
        self.assertEqual(first.outline, ["المقدمة 1 / 2", "الدرس الأول"])
        self.assertEqual(TrainingSlide.objects.count(), 2)

    def test_recrawl_keeps_downloads_for_same_image_and_resets_changed_one(self):
        training.import_crawl(crawl())
        TrainingSlide.objects.update(image_sha256="s" * 64, ocr_text="نص")
        training.import_crawl(crawl(images=("a.jpeg", "c.jpeg"), token="t2"))
        kept, changed = TrainingSlide.objects.order_by("position")
        self.assertEqual((kept.image_sha256, kept.ocr_text), ("s" * 64, "نص"))
        self.assertIn("token=t2", kept.image_url)
        self.assertEqual((changed.image_sha256, changed.ocr_text), ("", ""))

    def test_failed_lesson_crawl_does_not_delete_existing_slides(self):
        training.import_crawl(crawl())
        data = crawl()
        data[0]["lessons"][0]["steps"] = []
        training.import_crawl(data)
        self.assertEqual(TrainingSlide.objects.count(), 2)
        training.import_crawl(crawl(images=("a.jpeg",)))
        self.assertEqual(TrainingSlide.objects.count(), 1)

    def test_download_names_files_by_hash_and_resumes_after_failure(self):
        training.import_crawl(crawl(images=("a.jpeg", "b.JPG", "c.jpeg")))
        calls = []

        def read(url):
            calls.append(url)
            if "b.JPG" in url:
                raise OSError("network")
            return b"same-bytes"

        result = training.download_slides(read=read)
        self.assertEqual(result, {"downloaded": 2, "failed": 1, "remaining": 1})
        files = sorted(p.name for p in training.slides_dir().iterdir())
        self.assertEqual(len(files), 1)  # صورتان بالمحتوى نفسه ملف واحد
        self.assertTrue(files[0].endswith(".jpeg"))
        result = training.download_slides(read=lambda url: b"other")
        self.assertEqual(result, {"downloaded": 1, "failed": 0, "remaining": 0})
        self.assertEqual(len(calls), 3)  # المنزَّل سابقًا لا يُطلب ثانية

    def test_image_reader_refuses_other_hosts_and_plain_http(self):
        for url in ("https://example.com/x.jpeg", "http://firebasestorage.googleapis.com/x.jpeg",
                    "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                training._read_image(url)

    def test_ocr_manifest_is_per_unique_image_and_results_reach_every_slide(self):
        training.import_crawl(crawl(images=("a.jpeg", "b.JPG", "c.jpeg")))
        training.download_slides(read=lambda url: b"x" if "c.jpeg" not in url else b"y")
        manifest = training.ocr_manifest()
        self.assertEqual(len(manifest), 2)
        lines = [json.dumps({"sha256": manifest[0]["sha256"], "text": "مرحبا", "engine": "e"}),
                 json.dumps({"sha256": manifest[1]["sha256"], "error": "bad"}), ""]
        updated = training.import_ocr(lines)
        self.assertEqual(updated, TrainingSlide.objects.filter(ocr_text="مرحبا").count())
        self.assertEqual(len(training.ocr_manifest()), 1)  # الفاشلة تبقى للمحاولة التالية
        self.assertEqual(len(training.ocr_manifest(redo=True)), 2)

    def test_transcript_import_replaces_segments(self):
        path = self.local / "t.txt"
        path.write_text(
            "عنوان\n\n[00:00:05] مرحبا\n[01:02:03] شكرا\nسطر بلا توقيت\n", encoding="utf-8")
        for _ in range(2):
            recording, count = training.import_transcript(
                path, "k", "اللقاء الأول", category="المحترفين", held_on="2026-11-07")
        self.assertEqual(count, 2)
        self.assertEqual(TrainingRecording.objects.count(), 1)
        self.assertEqual(recording.duration_seconds, 3723)
        self.assertFalse(recording.reviewed)
        self.assertEqual(list(recording.segments.values_list("start_seconds", "text")),
                         [(5, "مرحبا"), (3723, "شكرا")])

    def test_command_runs_stages_and_reports_status(self):
        path = self.local / "crawl.json"
        path.write_text(json.dumps(crawl()), encoding="utf-8")
        out = StringIO()
        call_command("training_content", "crawl", str(path), stdout=out)
        call_command("training_content", "ocr", stdout=out)  # لا صور منزَّلة: لا يُشغَّل المحرك
        call_command("training_content", "status", stdout=out)
        status = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual((status["courses"], status["lessons"], status["slides"]), (1, 2, 2))
        self.assertEqual(status["lessons_without_slides"], 1)


class TrainingToolTests(TestCase):
    def test_ocr_text_is_ordered_top_down_and_right_to_left(self):
        tool = load_tool("training_ocr")
        texts = ["يسار", "أسفل", "يمين"]
        boxes = [[10, 10, 100, 40], [10, 80, 300, 110], [200, 12, 300, 42]]
        self.assertEqual(tool.ordered_text(texts, boxes), "يمين يسار\nأسفل")
        self.assertEqual(tool.ordered_text(texts, boxes, right_to_left=False), "يسار يمين\nأسفل")

    def test_ocr_tool_skips_images_already_read(self):
        tool = load_tool("training_ocr")
        with TemporaryDirectory() as folder:
            manifest, results = Path(folder) / "m.jsonl", Path(folder) / "r.jsonl"
            manifest.write_text(json.dumps({"sha256": "a", "path": "x"}) + "\n", encoding="utf-8")
            results.write_text(json.dumps({"sha256": "a", "text": "t"}) + "\n", encoding="utf-8")
            # كل الصور مقروءة: يخرج قبل استيراد المحرك
            self.assertEqual(tool.main([str(manifest), str(results)]), 0)

    def test_receiver_accepts_one_post_from_the_site_then_closes(self):
        tool = load_tool("training_crawl_receiver")
        with TemporaryDirectory() as folder:
            output = Path(folder) / "crawl.json"
            code = []
            thread = threading.Thread(target=lambda: code.append(
                tool.main([str(output), "--port", "8734", "--minutes", "0.2", "--token", "tk"])))
            thread.start()
            for _ in range(50):  # ينتظر بدء الاستماع قبل أول طلب
                try:
                    socket.create_connection(("127.0.0.1", 8734), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)

            def post(origin, body=b"[]", kind="application/json", path="/tk"):
                request = urllib.request.Request(
                    "http://127.0.0.1:8734" + path, data=body, method="POST",
                    headers={"Origin": origin, "Content-Type": kind})
                return urllib.request.urlopen(request, timeout=5).status  # noqa: S310

            with self.assertRaises(urllib.error.HTTPError):
                post("https://evil.example")
            with self.assertRaises(urllib.error.HTTPError):
                post("https://hamdanfsc.com", path="/wrong")
            self.assertFalse(output.exists())
            with urllib.request.urlopen("http://127.0.0.1:8734/", timeout=5) as page:  # noqa: S310
                self.assertIn(b"postMessage", page.read())
            # نموذج المتصفح: الناتج في حقل payload مرمَّزًا
            form = urllib.parse.urlencode({"payload": '[{"url": "/x"}]'}).encode()
            self.assertEqual(
                post("null", form, "application/x-www-form-urlencoded"), 200)
            thread.join(10)
            self.assertEqual(code, [0])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [{"url": "/x"}])
