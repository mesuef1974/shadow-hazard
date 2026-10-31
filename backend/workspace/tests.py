from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Project, Phase, Task, Decision, TaskUpdate


class WorkspacePermissionsTests(TestCase):
    def test_static_does_not_expose_documents(self):
        from django.contrib.staticfiles import finders
        self.assertIsNone(finders.find('brand/approved-v1/README.md'))
        self.assertIsNotNone(finders.find('brand/approved-v2/icon-small.svg'))

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='mesuef',role='supervisor')
        self.saud = User.objects.create_user(username='saud',role='student')
        self.mubarak = User.objects.create_user(username='mubarak',role='student')
        self.project = Project.objects.create(name='ظل الخطر')
        phase = Phase.objects.create(project=self.project,title='التجربة',objective='دليل',order=1)
        self.task = Task.objects.create(phase=phase,title='اختبار',description='معيار قبول',assignee=self.saud,creator=self.owner)

    def test_student_visibility_and_write_limits(self):
        self.client.force_login(self.mubarak)
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/').status_code,200)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/',{'body':'تعديل','state':'doing'}).status_code,403)
        self.assertEqual(self.client.get('/tasks/new/').status_code,403)
        self.assertEqual(self.client.get('/exhibition/').status_code,403)

    def test_assignee_can_submit_but_not_accept(self):
        self.client.force_login(self.saud)
        url=f'/tasks/{self.task.pk}/'
        self.assertEqual(self.client.post(url,{'body':'دليل','state':'review','hours':'1.5'}).status_code,302)
        self.client.post(url,{'body':'اعتماد غير مسموح','state':'accepted'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.state,'review')
        self.assertEqual(TaskUpdate.objects.count(),1)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url,{'body':'تمت المراجعة','state':'accepted'}).status_code,302)
        self.task.refresh_from_db();self.assertEqual(self.task.state,'accepted')

    def test_private_decisions_and_unclassified_documents(self):
        Decision.objects.create(project=self.project,title='PRIVATE-SENTINEL',body='secret',private=True,author=self.owner)
        self.client.force_login(self.saud)
        self.assertNotContains(self.client.get('/decisions/'),'PRIVATE-SENTINEL')
        self.assertEqual(self.client.get('/documents/blueprint/schema.md').status_code,404)

    def test_pages_and_anonymous_access(self):
        for path in ['/','/phases/','/tasks/','/decisions/','/library/','/exhibition/']:
            self.assertEqual(self.client.get(path).status_code,302)
        self.client.force_login(self.owner)
        for path in ['/','/phases/','/tasks/','/decisions/','/library/','/exhibition/','/tasks/new/']:
            self.assertEqual(self.client.get(path).status_code,200)
