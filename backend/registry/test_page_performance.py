from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from accounts.models import User
from workspace.models import Project
from workspace.memory import document


class LibraryPayloadTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Performance')
        self.client.force_login(User.objects.create(username='mesuef', role='supervisor'))

    def test_only_visible_files_are_hashed_and_receipt_search_defers_evidence(self):
        receipt = document(self.project, 'review', 'needle', 'needle receipt', {'large': 'x' * 100000})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for i in range(25):
                (root / f'{i:02}.md').write_text(f'# Document {i}', encoding='utf-8')
            original = Path.read_bytes
            reads = []
            def read(path):
                if path.parent == root:
                    reads.append(path.name)
                return original(path)
            with patch('registry.portal.ROOT', root), patch.object(Path, 'read_bytes', read):
                page = self.client.get('/library/')
                self.assertEqual(page.status_code, 200)
                self.assertEqual(len(reads), 10)
                self.assertEqual(page.context['page_obj'].paginator.count, 26)
                reads.clear()
                with CaptureQueriesContext(connection) as queries:
                    result = self.client.get('/library/', {'q': 'needle'})
                self.assertContains(result, f'__receipts__/{receipt.pk}.md')
                self.assertEqual(reads, [])
                receipt_sql = [q['sql'] for q in queries if 'FROM "workspace_projectdocumentation"' in q['sql']]
                self.assertTrue(receipt_sql)
                self.assertTrue(all('"evidence"' not in q for q in receipt_sql))
                self.assertEqual(self.client.get(f'/documents/__receipts__/{receipt.pk}.md').status_code, 200)
                self.client.force_login(User.objects.create(username='saud', role='student'))
                self.assertNotContains(self.client.get('/library/'), 'needle receipt')
