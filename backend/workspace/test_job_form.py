import uuid
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from execution_context.routing import build_plan
from .forms import ModelJobForm
from .models import Project, ModelPolicy, SessionCharter, ModelJob


class JobFormValidationTests(TestCase):
    def setUp(self):
        project = Project.objects.create(name='Synthetic project')
        policy = ModelPolicy.objects.create(category='form-test', title='Test')
        self.charter = SessionCharter.objects.create(project=project, policy=policy,
            key='test', name='Test', thread_id=str(uuid.uuid4()))
        self.owner = get_user_model().objects.create_user(username='mesuef', role='supervisor')
        patcher = patch('execution_context.routing.available_plugins', return_value=['one', 'two', 'three'])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = dict(submission_key=str(uuid.uuid4()), charter=self.charter.pk,
            prompt='Synthetic task', mode='workspace-write', task_kind='connected', plugins=['one'])

    def form(self, **changes):
        form = ModelJobForm(dict(self.data, **changes))
        form.fields['charter'].queryset = SessionCharter.objects.all()
        return form

    def test_registered_connectors_over_limit_have_specific_error(self):
        form = self.form(plugins=['one', 'two', 'three'])
        self.assertFalse(form.is_valid())
        self.assertIn('موصلين كحد أقصى', form.errors['plugins'][0])
        with self.assertRaisesRegex(ValueError, 'موصلين كحد أقصى'):
            build_plan(self.charter, 'Test', kind='connected', plugins=['one', 'two', 'three'])

    def test_unknown_connector_does_not_expand_choices(self):
        self.assertFalse(self.form(plugins=['unregistered']).is_valid())
        with self.assertRaisesRegex(ValueError, 'موصل غير مسجل'):
            build_plan(self.charter, 'Test', kind='connected', plugins=['unregistered'])

    def test_kind_and_connector_requirements(self):
        self.assertTrue(self.form(plugins=['one', 'two']).is_valid())
        self.assertIn('plugins', self.form(task_kind='text').errors)
        self.assertIn('plugins', self.form(plugins=[]).errors)
        self.assertTrue(self.form(task_kind='text', plugins=[]).is_valid())

    def test_skill_limit_has_field_error(self):
        form = self.form(skills=list(ModelJobForm.SKILLS))
        self.assertFalse(form.is_valid())
        self.assertIn('ثلاث مهارات', form.errors['skills'][0])

    @patch('workspace.views.enqueue')
    def test_invalid_submission_keeps_selections_and_does_not_enqueue(self, enqueue):
        self.client.force_login(self.owner)
        response = self.client.post('/sessions/jobs/', dict(self.data, plugins=['one', 'two', 'three']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'موصلين كحد أقصى')
        self.assertContains(response, '<details class="usage-fold" open>')
        self.assertEqual(response.context['form']['submission_key'].value(), self.data['submission_key'])
        self.assertEqual(str(response.context['form']['charter'].value()), str(self.charter.pk))
        enqueue.assert_not_called()
        self.assertEqual(ModelJob.objects.count(), 0)

    @patch('workspace.views.enqueue')
    def test_valid_submission_preserves_destination_and_execution_mode(self, enqueue):
        self.client.force_login(self.owner)
        response = self.client.post('/sessions/jobs/', self.data)
        self.assertEqual(response.status_code, 302)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0].pk, self.charter.pk)
        self.assertEqual(enqueue.call_args.args[3], 'workspace-write')
