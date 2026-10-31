from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from operations.models import AutomationIssue, BackupPolicy, BackupRun
from .models import ModelJob, ModelPolicy, Project, SessionCharter


class AutomationUiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.supervisor = User.objects.create_user(username='mesuef', role='supervisor')
        self.student = User.objects.create_user(username='saud', role='student')
        project = Project.objects.create(name='ظل الخطر')
        policy = ModelPolicy.objects.create(category='automation-ui', title='متابعة الأتمتة')
        charter = SessionCharter.objects.create(
            key='automation-ui', name='ميثاق المتابعة', project=project, policy=policy,
            scope='عرض حالة موثقة', rules='قراءة فقط', thread_id='thread-safe-id',
        )
        self.job = ModelJob.objects.create(
            charter=charter, owner=self.supervisor, prompt='لا تعرض هذا الطلب',
            requested_model='model-requested', requested_thinking='medium',
            observed_model='model-observed', token_usage={'input_tokens': 8, 'output_tokens': 3},
            result='نتيجة مثبتة', state='completed', finished_at=timezone.now(),
        )

    def test_access_is_supervisor_only(self):
        self.assertEqual(self.client.get('/sessions/automation/').status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/sessions/automation/').status_code, 403)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get('/sessions/automation/').status_code, 200)

    def test_renders_evidence_backed_status_and_separate_backup_records(self):
        BackupPolicy.objects.update_or_create(key='default', defaults={'enabled': True})
        source = BackupRun.objects.create(kind='scheduled', state='success', integrity_result='ok', size_bytes=1024)
        BackupRun.objects.create(kind='restore', state='success', source=source)
        AutomationIssue.objects.create(fingerprint='test-issue', code='test', title='تأخر في التحقق',
                                       evidence={'secret_path': 'must-not-render'})
        self.client.force_login(self.supervisor)
        response = self.client.get('/sessions/automation/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'دورة المهمة')
        self.assertContains(response, 'التفكير المطلوب')
        self.assertContains(response, 'model-requested')
        self.assertContains(response, 'نتيجة مثبتة')
        self.assertContains(response, 'لم تُجمع مع عينات لوحة استهلاك الرموز')
        self.assertContains(response, 'الاستعادة المنفصلة')
        self.assertContains(response, 'لا تستبدل قاعدة التشغيل')
        self.assertNotContains(response, 'must-not-render')
        self.assertNotContains(response, 'لا تعرض هذا الطلب')
        self.assertContains(response, 'لا تعرض الصفحة نجاحًا مؤكدًا')
        self.assertContains(response,'هذه الآلية لا توفر نسخة PostgreSQL')

    def test_mobile_and_long_lists_are_scoped_and_collapsible(self):
        self.client.force_login(self.supervisor)
        response = self.client.get('/sessions/automation/')
        self.assertContains(response, '@media(max-width:700px)')
        self.assertContains(response, 'grid-template-columns:1fr')
        self.assertContains(response, '<details', html=False)
        self.assertContains(response, 'overflow-x:auto')
        self.assertContains(response, 'aria-labelledby="automation-cycle-title"')

    def test_empty_state_does_not_claim_completion(self):
        ModelJob.objects.all().delete()
        self.client.force_login(self.supervisor)
        response = self.client.get('/sessions/automation/')
        self.assertContains(response, 'لا توجد طلبات أتمتة مسجلة بعد')
        self.assertNotContains(response, 'اكتمل التحقق بالأدلة')
