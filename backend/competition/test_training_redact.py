import json
from datetime import UTC, datetime
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from competition import training
from competition.models import TrainingCourse, TrainingLesson, TrainingSlide

NOW = datetime(2026, 11, 12, 12, 30, tzinfo=UTC)
HIDDEN = training.REDACTED
# قيم مصطنعة للاختبار، لا تخصّ خدمة حقيقية.
TOKEN = "0123456789abcdef0123456789abcdef"


class RedactSecretsTests(SimpleTestCase):
    def test_hides_quoted_and_unquoted_secret_values(self):
        cases = {
            'const char* password = "hunter42x";': f'const char* password = "{HIDDEN}";',
            "char pass[] = “abcd1234”;": f"char pass[] = “{HIDDEN}”;",
            "char pass[] = abcd1234;": f"char pass[] = {HIDDEN};",
            "char WiFiPassword[] = \"9012345673\";": f'char WiFiPassword[] = "{HIDDEN}";',
            "AUTH_TOKEN: 'tok-en-value'": f"AUTH_TOKEN: '{HIDDEN}'",
            f"appid={TOKEN}&units=metric": f"appid={HIDDEN}&units=metric",
            f"apiKey = {TOKEN}": f"apiKey = {HIDDEN}",
            "weather?q=Dubai&APPID=13fab45cd9": f"weather?q=Dubai&APPID={HIDDEN}",
        }
        for text, expected in cases.items():
            self.assertEqual(training.redact_secrets(text), expected)

    def test_keeps_code_that_is_not_a_secret(self):
        for text in (
            'const char* ssid = "HomeNetwork";',
            "WiFi.begin(ssid, password);",
            "char customKey = customKeypad.getKey();",
            "int key = analogRead(A0);",
            "password = const char",
            "pinMode(13, OUTPUT); // 74HC595",
            "شرح بالعربية عن كلمة المرور دون قيمة",
        ):
            self.assertEqual(training.redact_secrets(text), text)

    def test_hides_certificate_blocks_and_is_idempotent(self):
        text = ("قبل\n-----BEGIN CERTIFICATE-----\nMIIabc+def/123\nxyz==\n"
                "-----END CERTIFICATE-----\nبعد")
        once = training.redact_secrets(text)
        self.assertEqual(once, f"قبل\n{HIDDEN}\nبعد")
        mixed = training.redact_secrets(f'password = "hunter42x"; key={TOKEN}')
        self.assertEqual(training.redact_secrets(once), once)
        self.assertEqual(training.redact_secrets(mixed), mixed)
        self.assertNotIn("hunter42x", mixed)
        self.assertNotIn(TOKEN, mixed)


class RedactStoredTests(TestCase):
    def setUp(self):
        course = TrainingCourse.objects.create(
            key="c", kind="course", title="دورة", source_url="https://x/c", scraped_at=NOW)
        self.lesson = TrainingLesson.objects.create(
            course=course, key="a", position=1, title="درس", source_url="https://x/a")

    def test_import_paths_store_redacted_text(self):
        training._save_slide(self.lesson, 1, {"img": ["https://x/1.png"],
                                              "text": 'password = "hunter42x"'})
        slide = TrainingSlide.objects.get()
        self.assertNotIn("hunter42x", slide.text)
        TrainingSlide.objects.update(image_sha256="s")
        training.import_ocr([json.dumps({"sha256": "s", "text": f"key={TOKEN}"})], now=NOW)
        slide.refresh_from_db()
        self.assertEqual(slide.ocr_text, f"key={HIDDEN}")

    def test_redact_command_cleans_stored_rows_once(self):
        TrainingSlide.objects.create(lesson=self.lesson, position=1, text="نص عادي",
                                     ocr_text='pass[] = "abcd1234"')
        TrainingSlide.objects.create(lesson=self.lesson, position=2, text="نص عادي")
        out = StringIO()
        call_command("training_content", "redact", stdout=out)
        call_command("training_content", "redact", stdout=out)
        first, second = (json.loads(line) for line in out.getvalue().splitlines())
        self.assertEqual((first, second), ({"slides_changed": 1}, {"slides_changed": 0}))
        self.assertNotIn("abcd1234", TrainingSlide.objects.get(position=1).ocr_text)
