from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Project, Phase, Task, Decision, TaskUpdate


class WorkspacePermissionsTests(TestCase):
    def test_routing_policy_and_sessions_access(self):
        from .models import ModelPolicy
        from .routing import recommend
        ModelPolicy.objects.create(category='frontend',title='الواجهة')
        self.assertEqual(recommend('frontend')['model'],'gpt-6-luna')
        self.assertTrue(recommend('frontend')['requires_owner_launch'])
        self.client.force_login(self.saud)
        self.assertEqual(self.client.get('/sessions/').status_code,403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/sessions/').status_code,200)

    def test_main_and_specialized_sessions_visible_only_to_supervisor(self):
        from django.utils import timezone
        from registry.models import Actor, ExecutionSession
        from token_usage.models import TrackedSession, TokenSample
        from .models import ModelPolicy, SessionCharter

        thread_id='01a18e02-0487-7180-9aa0-f932b22c8ef4'
        actor=Actor.objects.create(key='codex-main-test',name='Codex assistant',kind='agent')
        ExecutionSession.objects.create(external_key=thread_id,name='ظل الخطر',actor=actor,client='Codex desktop',model='unverified declaration')
        tracked=TrackedSession.objects.create(external_id=thread_id,name='ظل الخطر',project=self.project)
        TokenSample.objects.create(session=tracked,source_id=1,sampled_at=timezone.now(),model='gpt-6-luna',total=10,input=4,cached=0,output=6,reasoning=2,context=20,context_limit=100)
        coordination_policy=ModelPolicy.objects.create(category='coordination-test',title='التنسيق',model='gpt-6-luna',thinking='low')
        SessionCharter.objects.create(key='coordination-test',name='جلسة التنسيق العام',project=self.project,policy=coordination_policy,
            scope='التنسيق',rules='حدود التنسيق',thread_id=thread_id,state='ready_waiting_for_task')
        policy=ModelPolicy.objects.create(category='frontend-test',title='الواجهة',model='gpt-6-luna',thinking='low')
        SessionCharter.objects.create(key='frontend-test',name='جلسة الواجهة',project=self.project,policy=policy,scope='الواجهة',rules='حدود الجلسة',
            thread_id='01a190d6-59eb-7561-85d9-d58a7d49fe65',state='ready_waiting_for_task')
        unlinked_policy=ModelPolicy.objects.create(category='prototype-test',title='ميثاق تجريبي')
        SessionCharter.objects.create(key='prototype-test',name='ميثاق تجريبي بلا معرف',project=self.project,
            policy=unlinked_policy,scope='تجربة',rules='ميثاق غير مربوط')

        self.client.force_login(self.owner)
        response=self.client.get('/sessions/')
        self.assertContains(response,'جلسة التنسيق العام')
        self.assertContains(response,'ظل الخطر')
        self.assertContains(response,'gpt-6-luna')
        self.assertContains(response,'جلسة الواجهة')
        self.assertContains(response,'ميثاق تجريبي بلا معرف')
        self.assertContains(response,'غير جاهز — يحتاج معرف جلسة')

        self.client.force_login(self.saud)
        self.assertEqual(self.client.get('/sessions/').status_code,403)

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

    def test_sessions_render_database_routing_policy_and_read_only_limit(self):
        from execution_context.models import SessionRoutingPolicy
        policy=SessionRoutingPolicy.objects.get(category='code')
        policy.max_attempts=3;policy.enabled=False;policy.save()
        self.client.force_login(self.owner)
        response=self.client.get('/sessions/')
        self.assertContains(response,'سياسة التوجيه الفعلية')
        self.assertContains(response,'حد المحاولات: 3')
        self.assertContains(response,'السياسة متوقفة')
        self.assertContains(response,'gpt-6.1-sol')
        self.assertContains(response,'مهام القراءة فقط تعمل في حاوية منفصلة')


    def test_owner_tasks_are_separate_and_private(self):
        private = Task.objects.create(phase=self.task.phase, title='OWNER_ONLY_SENTINEL',
            description='private', assignee=self.saud, creator=self.owner, owner_only=True, state='review')
        for user in [self.owner, self.saud]:
            self.client.force_login(user)
            for path in ['/', '/phases/', '/tasks/']:
                self.assertNotContains(self.client.get(path), private.title)
        self.assertEqual(self.client.get('/tasks/?scope=owner').status_code, 403)
        url=f'/tasks/{private.pk}/'
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'body':'attempt','state':'doing'}).status_code, 403)
        private.refresh_from_db(); self.assertEqual(private.state, 'review')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/tasks/?scope=owner&state=review'), private.title)
        self.assertNotContains(self.client.get('/tasks/?scope=owner'), self.task.title)
        self.assertContains(self.client.get(url), private.title)
        self.assertEqual(self.client.get('/').context['total'], 1)


    def test_task_reclassified_private_during_update_is_denied(self):
        from unittest.mock import patch
        from django.shortcuts import get_object_or_404
        def reclassify(*args, **kwargs):
            item=get_object_or_404(*args, **kwargs)
            Task.objects.filter(pk=item.pk).update(owner_only=True)
            return item
        self.client.force_login(self.saud)
        with patch('workspace.views.get_object_or_404', side_effect=reclassify):
            response=self.client.post(f'/tasks/{self.task.pk}/', {'body':'attempt','state':'doing'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TaskUpdate.objects.filter(task=self.task).exists())
