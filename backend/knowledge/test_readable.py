import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from workspace.memory import document
from workspace.models import Project

from .readable import GLUED, has_glued_text, headline, readable
from .services import digest, index_project

GLUED_SUMMARY = 'تحققحيمننقلقناةالمالكالمكتملسابقاإلىالمشغل وتفعيلcoordination. لا نقلأوإرسالحيجديد.'


def letters(text):
    return re.sub(r'[\sـ]', '', text)


class ReadableTests(SimpleTestCase):
    def test_spaces_are_added_and_letters_never_change(self):
        repaired = readable(GLUED_SUMMARY)
        self.assertFalse(has_glued_text(repaired))
        self.assertIn('قناة المالك', repaired)
        self.assertIn('وتفعيل coordination', repaired)
        self.assertEqual(letters(repaired), letters(GLUED_SUMMARY))

    def test_healthy_text_is_returned_as_written(self):
        healthy = 'نص سليم فيه كلمة نادرة بالمستشعراتية و50اختبارا'
        self.assertEqual(readable(healthy), healthy)

    def test_headline_stops_at_the_first_sentence(self):
        self.assertEqual(headline('مراجعة مستقلة؛ تفاصيل أخرى'), 'مراجعة مستقلة')

    def test_headline_cuts_on_a_word(self):
        title = headline('كلمة ' * 40)
        self.assertLessEqual(len(title), 91)
        self.assertTrue(title.endswith('كلمة…'))

    def test_project_documents_have_no_glued_text(self):
        for path in (Path(settings.BASE_DIR).parent / 'docs').rglob('*.md'):
            if 'source' not in path.parts:
                self.assertIsNone(GLUED.search(path.read_text(encoding='utf-8')), path)


class SealedRecordTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='مشروع')
        self.owner = get_user_model().objects.create_user(username='mesuef', role='supervisor')

    def sealed(self, summary):
        record = document(self.project, 'test', 'record', 'ملخص مؤقت', {'status': 'done'})
        payload = {'summary': summary, 'evidence': record.evidence}
        type(record).objects.filter(pk=record.pk).update(
            summary=summary, digest=digest(json.dumps(payload, sort_keys=True, ensure_ascii=False)))
        index_project(self.project, [record.pk])
        return record

    def test_glued_summary_is_refused_on_write(self):
        with self.assertRaisesMessage(ValueError, 'ملتصق'):
            document(self.project, 'test', 'glued', GLUED_SUMMARY, {'status': 'done'})

    def test_old_record_reads_well_and_keeps_its_original(self):
        record = self.sealed(GLUED_SUMMARY)
        self.client.force_login(self.owner)
        response = self.client.get(f'/knowledge/documentation/{record.pk}/')
        self.assertContains(response, 'قناة المالك')
        self.assertContains(response, 'أُضيفت المسافات للعرض فقط')
        self.assertContains(response, GLUED_SUMMARY)
        self.assertContains(response, '<details class="knowledge-raw">')
        record.refresh_from_db()
        self.assertEqual(record.summary, GLUED_SUMMARY)

    def test_long_summary_is_not_the_page_title(self):
        record = self.sealed('جملة أولى قصيرة. ' + 'تفاصيل طويلة ' * 30)
        self.client.force_login(self.owner)
        response = self.client.get(f'/knowledge/documentation/{record.pk}/')
        self.assertContains(response, '<h1>جملة أولى قصيرة</h1>', html=True)
        self.assertContains(response, 'class="knowledge-summary"')
