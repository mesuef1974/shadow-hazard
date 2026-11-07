from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from workspace.models import Decision, Phase, Project, Task, TaskUpdate


class PriorDesignArchiveTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username='mesuef', role='supervisor')
        self.student = users.create_user(username='saud', role='student')
        self.project = Project.objects.create(name='test')

    def test_current_deck_is_bus_design_and_prior_deck_is_kept(self):
        self.client.force_login(self.owner)
        current = self.client.get('/exhibition/')
        self.assertContains(current, 'الرصد والتحذير على الحافلة نفسها')
        self.assertNotContains(current, 'من زاويتين')
        prior = self.client.get('/exhibition/archive/')
        self.assertContains(prior, 'أرشيف: هذا عرض التصميم السابق')
        self.assertContains(prior, 'id="v4-results"')

    def test_student_sees_archive_page_without_supervisor_material(self):
        self.client.force_login(self.student)
        page = self.client.get('/archive/')
        self.assertContains(page, 'أرشيف النموذج السابق')
        self.assertNotContains(page, '/exhibition/archive/')
        self.assertNotContains(page, '__receipts__')
        self.assertEqual(self.client.get('/exhibition/archive/').status_code, 403)

    def test_command_adds_records_once_and_keeps_task_state(self):
        phase = Phase.objects.create(project=self.project, title='p', objective='o', order=1)
        task = Task.objects.create(phase=phase, title='الفكرة الأساسية', description='الأصل', assignee=self.owner, creator=self.owner, state='review')
        for _ in range(2):
            call_command('archive_prior_design', stdout=StringIO())
        task.refresh_from_db()
        self.assertEqual((task.description, task.state), ('الأصل', 'review'))
        self.assertEqual(TaskUpdate.objects.filter(task=task).count(), 1)
        self.assertEqual(Decision.objects.count(), 1)
