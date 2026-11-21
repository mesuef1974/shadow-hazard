import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import export_idea_review as export  # noqa: E402


class FormLimitTests(unittest.TestCase):
    def test_field_at_limit_passes_and_one_over_fails(self):
        self.assertEqual(export.over_limit([('فكرة المشروع', 'ن' * 1000)]), [])
        self.assertEqual(export.over_limit([('فكرة المشروع', 'ن' * 1001)]),
                         [('فكرة المشروع', 1001, 1000)])

    def test_name_has_its_own_limit(self):
        self.assertEqual(export.over_limit([(export.NAME, 'ن' * 101)]),
                         [(export.NAME, 101, 100)])

    def test_newline_counts_as_two_characters(self):
        body = 'ن' * 499 + '\n' + 'ن' * 499
        self.assertEqual(export.form_length(body), 1000)
        self.assertEqual(export.over_limit([('فكرة المشروع', body + 'ن')]),
                         [('فكرة المشروع', 1001, 1000)])


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.found = list(export.fields(export.SOURCE.read_text(encoding='utf-8')))

    def test_draft_fits_the_platform_form(self):
        self.assertEqual(export.over_limit(self.found), [])

    def test_form_text_matches_the_draft(self):
        self.assertEqual(export.TARGET.read_text(encoding='utf-8'), export.render(self.found),
                         'أعد التوليد: python tools/export_idea_review.py')


if __name__ == '__main__':
    unittest.main()
