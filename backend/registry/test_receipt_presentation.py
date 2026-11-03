from django.test import TestCase
from accounts.models import User
from workspace.models import Project
from workspace.memory import document


class ReceiptPresentationTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Receipt presentation')
        self.client.force_login(User.objects.create(username='mesuef', role='supervisor'))

    def test_readable_supplement_displays_on_original_url_and_preserves_download(self):
        original = document(self.project, 'coordination', 'plan', 'Original summary', {'tasks': [7]})
        document(self.project, 'coordination', 'readable-plan', 'Readable summary', {
            'source_receipt': original.pk, 'report_title': 'Readable plan',
            'report_sections': [{'title': 'Phase one', 'body': 'Acceptance criteria'}]})
        document(self.project, 'coordination', 'later-audit', 'Unrelated supplement', {'source_receipt': original.pk})
        for index, sections in enumerate(([], None, {'title': 'wrong shape'}, ['wrong section'])):
            document(self.project, 'coordination', f'invalid-report-{index}', 'Invalid supplement',
                     {'source_receipt': original.pk, 'report_sections': sections})
        url = f'/documents/__receipts__/{original.pk}.md'
        response = self.client.get(url)
        self.assertContains(response, 'Readable plan')
        self.assertContains(response, 'Acceptance criteria')
        self.assertContains(response, '<details>')
        download = self.client.get(url+'?download=1').content.decode()
        self.assertIn('Original summary', download)
        self.assertNotIn('Readable summary', download)

    def test_generic_receipt_keeps_raw_evidence_collapsed(self):
        record = document(self.project, 'coordination', 'generic', 'Readable summary', {'technical': True})
        response = self.client.get(f'/documents/__receipts__/{record.pk}.md')
        self.assertContains(response, '<h2>ملخص الوثيقة</h2>')
        self.assertContains(response, '<pre dir="ltr">')
        self.assertNotContains(response, '<details open')
