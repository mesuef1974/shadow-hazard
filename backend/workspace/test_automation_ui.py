from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from operations.models import AutomationIssue, BackupPolicy, BackupRun
from .models import ModelJob, ModelPolicy, Project, SessionCharter


class AutomationUiTests(TestCase):
    def test_expired_heartbeat_is_unavailable_in_automation_page(self):
        from datetime import timedelta
        from .models import DispatcherHeartbeat
        row = DispatcherHeartbeat.objects.create(key='local', pid=999999, state='running')
        DispatcherHeartbeat.objects.filter(pk=row.pk).update(updated_at=timezone.now()-timedelta(minutes=5))
        self.client.force_login(self.supervisor)
        self.assertContains(self.client.get('/sessions/automation/'), 'غير متاح — النبض متقادم')

    def test_operational_review_is_shown_inside_original_job_card(self):
        from unittest.mock import patch
        self.client.force_login(self.supervisor)
        proof = dict(receipt=417, journal=2, activation_receipt=412, renewal_valid=True,
            message=28, dispatch=29, checked_at=timezone.now())
        with patch('workspace.operational_reviews.operational_reviews', return_value={self.job.pk: proof}):
            response = self.client.get('/sessions/automation/')
        for text in ('#417', '#412', 'الرسالة #28', 'التسليم #29', 'سجل النقل #2'):
            self.assertContains(response, text)
        self.assertNotContains(response, 'لا توجد مراجعة مرتبطة مسجلة لهذا الطلب.')
    def test_map_and_assets_require_supervisor_and_open_as_dedicated_page(self):
        urls=['/sessions/automation-model/','/sessions/automation-model/assets/automation-map.css',
              '/sessions/automation-model/assets/automation-map.js']
        for url in urls:self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.student)
        for url in urls:self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.supervisor)
        response=self.client.get(urls[0])
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['X-Frame-Options'],'DENY')
        self.assertContains(response,'/static/registry/automation-model.css')
        self.assertContains(response,urls[2])
        self.assertContains(response,'/static/brand/approved-v2/navigation-white.svg')
        self.assertContains(response,'aria-label="التنقل الرئيسي"')
        self.assertContains(response,'id="theme-toggle"')
        self.assertContains(response,'href="/sessions/" aria-current="page"')
        self.assertNotContains(response,'<iframe')
        self.assertEqual(response.content.decode().count('<main'),1)
        self.assertEqual(response.content.decode().count('<h1'),1)
        self.assertContains(response,'انتظار 30 ثانية')
        self.assertContains(response,'id="release-flow"')
        self.assertContains(response,'id="operator-flow"')
        self.assertContains(response,'id="authority-flow"')
        self.assertContains(response,'id="operator-details"')
        self.assertContains(response,'data-node="quiet"')
        self.assertContains(response,'data-node="wake"')
        self.assertContains(response,'تجديد تلقائي وفق سياسة معتمدة')
        self.assertContains(response,'Luna بتفكير منخفض')
        self.assertNotContains(response,'تجديد يدوي مشروط بقرار المالك')
        self.assertNotContains(response,'العتبتان غير مفعّلتين حاليًا')
        self.assertContains(response,'الدمج المحلي')
        self.assertContains(response,'غير مؤتمت حاليًا')
        self.assertContains(self.client.get(urls[1]),'/static/brand/approved-v1/tokens.css')
        self.assertEqual(self.client.get(urls[2]).status_code,200)
        self.assertEqual(self.client.get('/sessions/automation-model/assets/unknown.txt').status_code,404)
        sessions=self.client.get('/sessions/')
        self.assertContains(sessions,'نموذج الأوتوميشن')
        self.assertContains(sessions,'href="/sessions/automation-model/"')
        self.assertNotContains(sessions,'<iframe')

    def test_coordination_card_uses_current_charter_binding(self):
        from registry.models import Actor,ExecutionSession
        from token_usage.models import TrackedSession
        charter=self.job.charter
        SessionCharter.objects.create(key='coordination',name='التنسيق',project=charter.project,
            policy=charter.policy,thread_id='replacement-coordination')
        actor=Actor.objects.create(key='test-agent',name='test',kind='agent')
        for key,name in [('replacement-coordination','التنسيق الجديد'),
                         ('01a18e02-0487-7180-9aa0-f932b22c8ef4','تنسيق قديم')]:
            ExecutionSession.objects.create(external_key=key,name=name,actor=actor,client='Codex',model='test')
            TrackedSession.objects.create(external_id=key,name=name,project=charter.project)
        self.client.force_login(self.supervisor)
        response=self.client.get('/sessions/')
        self.assertContains(response,'التنسيق الجديد')
        self.assertNotContains(response,'تنسيق قديم')

    def test_map_styles_are_only_loaded_by_map_page(self):
        self.client.force_login(self.supervisor)
        self.assertContains(self.client.get('/sessions/automation-model/'),'registry/automation-model.css')
        for path in ['/','/sessions/','/sessions/automation/','/usage/']:
            with self.subTest(path=path):
                response=self.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertNotContains(response,'registry/automation-model.css')
                self.assertNotContains(response,'assets/automation-map.js')

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

    def test_review_receipt_is_attached_to_original_without_mutating_job(self):
        from operations.models import ReviewAssignment
        from .models import ProjectDocumentation
        review = ModelJob.objects.create(charter=self.job.charter, owner=self.supervisor,
            prompt='مراجعة مستقلة', requested_model='review-model', requested_thinking='medium')
        link = ReviewAssignment.objects.create(source_job=self.job, review_job=review)
        self.client.force_login(self.supervisor)
        response = self.client.get('/sessions/automation/')
        self.assertContains(response, 'لم تُسجل نتيجة مراجعة لهذا الطلب بعد')
        receipt = ProjectDocumentation.objects.create(project=self.job.charter.project,
            session_key='review', task_key=f'automatic-review:{self.job.pk}',
            summary='نتيجة مراجعة الأصل فقط', evidence={'decision': 'BLOCKED'}, digest='a' * 64)
        self.assertEqual(link.result_document.pk, receipt.pk)
        response = self.client.get('/sessions/automation/')
        original = next(row for row in response.context['jobs'] if row['id'] == self.job.pk)
        self.assertEqual(original['review']['receipt'], receipt.pk)
        self.assertEqual(original['review']['job_id'], review.pk)
        self.assertEqual(original['review']['decision'], 'BLOCKED')
        self.assertContains(response, 'نتيجة مراجعة الأصل فقط')
        review_row = next(row for row in response.context['jobs'] if row['id'] == review.pk)
        self.assertIsNone(review_row['review'])
        self.job.refresh_from_db()
        self.assertEqual(self.job.state, 'completed')
        self.assertEqual(self.job.token_usage, {'input_tokens': 8, 'output_tokens': 3})
        revised = ProjectDocumentation.objects.create(project=self.job.charter.project,
            session_key='review', task_key=f'automatic-review:{self.job.pk}:revision:counter-proof',
            summary='مراجعة لاحقة بعد تصحيح الدليل', evidence={'decision': 'CONDITIONAL'}, digest='b' * 64)
        response = self.client.get('/sessions/automation/')
        original = next(row for row in response.context['jobs'] if row['id'] == self.job.pk)
        self.assertEqual(original['review']['receipt'], revised.pk)
        self.assertEqual(link.result_document.pk, revised.pk)
        self.assertTrue(ProjectDocumentation.objects.filter(pk=receipt.pk).exists())

    def test_review_lookup_batches_queries_and_scopes_document_to_project(self):
        from operations.models import ReviewAssignment
        from .models import ProjectDocumentation
        from .views import _automation_reviews
        review = ModelJob.objects.create(charter=self.job.charter, owner=self.supervisor,
            prompt='مراجعة', requested_model='review-model', requested_thinking='low')
        originals = [self.job]
        for index in range(4):
            originals.append(ModelJob.objects.create(charter=self.job.charter, owner=self.supervisor,
                prompt=f'أصل {index}', requested_model='model', requested_thinking='low'))
        for original in originals:
            ReviewAssignment.objects.create(source_job=original, review_job=review)
        ProjectDocumentation.objects.create(project=Project.objects.create(name='مشروع آخر'),
            session_key='review', task_key=f'automatic-review:{self.job.pk}',
            summary='نتيجة خارج المشروع', evidence={'decision': 'PASS'}, digest='b' * 64)
        with self.assertNumQueries(2):
            rows = _automation_reviews(originals)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row['receipt'] is None for row in rows.values()))

    def test_review_decisions_are_translated_without_changing_recorded_values(self):
        from operations.models import ReviewAssignment
        from .models import ProjectDocumentation
        review = ModelJob.objects.create(charter=self.job.charter, owner=self.supervisor,
            prompt='مراجعة', requested_model='review-model', requested_thinking='low')
        ReviewAssignment.objects.create(source_job=self.job, review_job=review)
        receipt = ProjectDocumentation.objects.create(project=self.job.charter.project,
            session_key='review', task_key=f'automatic-review:{self.job.pk}',
            summary='', evidence={}, digest='c' * 64)
        self.client.force_login(self.supervisor)
        for code, label in [('FAIL', 'لم يجتز المراجعة'), ('BLOCKED', 'معلّق بعائق'), ('CONDITIONAL', 'مشروط')]:
            with self.subTest(code=code):
                receipt.summary = code + ': ملخص المراجعة'
                receipt.evidence = {'decision': code}
                receipt.save()
                response = self.client.get('/sessions/automation/')
                original = next(row for row in response.context['jobs'] if row['id'] == self.job.pk)
                self.assertEqual(original['review']['decision'], code)
                self.assertEqual(original['review']['decision_label'], label)
                self.assertContains(response, label + ': ملخص المراجعة')
                self.assertNotContains(response, code + ': ملخص المراجعة')
                receipt.refresh_from_db()
                self.assertEqual(receipt.evidence['decision'], code)
                self.assertEqual(receipt.summary, code + ': ملخص المراجعة')

    def test_violation_labels_are_arabic_without_mutating_snapshot(self):
        from unittest.mock import patch
        from operations.services import task_snapshot
        snapshot = task_snapshot(self.job)
        codes = ['requested_observed_model_mismatch', 'observed_thinking_missing']
        snapshot['violations'] = codes.copy()
        snapshot['stages']['verification']['evidence'] = [{'violations': codes.copy()}]
        self.client.force_login(self.supervisor)
        with patch('operations.services.task_snapshot', return_value=snapshot):
            response = self.client.get('/sessions/automation/')
        self.assertContains(response, 'النموذج المرصود يخالف المطلوب')
        self.assertContains(response, 'مستوى التفكير المرصود مفقود')
        self.assertNotContains(response, 'requested_observed_model_mismatch')
        self.assertEqual(snapshot['violations'], codes)
        self.assertEqual(snapshot['stages']['verification']['evidence'][0]['violations'], codes)
        self.assertEqual(response.context['jobs'][0]['violations'], codes)

    def test_evidence_field_names_are_arabic_and_source_values_are_preserved(self):
        from unittest.mock import patch
        from copy import deepcopy
        from operations.services import task_snapshot
        from .views import AUTOMATION_EVIDENCE_LABELS
        snapshot = task_snapshot(self.job)
        original_record = {name: f'value-{name}' for name in AUTOMATION_EVIDENCE_LABELS if name != 'violations'}
        snapshot['stages']['verification']['evidence'] = [deepcopy(original_record)]
        before = deepcopy(snapshot)
        self.client.force_login(self.supervisor)
        with patch('operations.services.task_snapshot', return_value=snapshot):
            response = self.client.get('/sessions/automation/')
        evidence = response.context['jobs'][0]['stages'][5]['evidence'][0]
        for name, value in original_record.items():
            self.assertEqual(evidence[AUTOMATION_EVIDENCE_LABELS[name]], value)
        self.assertEqual(snapshot, before)
        self.assertContains(response, 'معرّف الدور المتحقق منه')
        self.assertContains(response, 'التفكير المرصود')

    def test_evidence_enum_values_translate_without_mutating_source(self):
        from unittest.mock import patch
        from copy import deepcopy
        from operations.services import task_snapshot
        snapshot = task_snapshot(self.job)
        snapshot['stages']['verification']['evidence'] = [
            {'state': 'completed', 'requested_thinking': 'medium', 'observed_thinking': 'low',
             'requested_model': 'model-original', 'attempt_no': 2},
            {'state': 'evidence_rejected', 'requested_thinking': 'high'},
        ]
        before = deepcopy(snapshot)
        self.client.force_login(self.supervisor)
        with patch('operations.services.task_snapshot', return_value=snapshot):
            response = self.client.get('/sessions/automation/')
        records = response.context['jobs'][0]['stages'][5]['evidence']
        self.assertEqual(records[0], {'الحالة': 'انتهى التنفيذ', 'التفكير المطلوب': 'متوسط',
            'التفكير المرصود': 'منخفض', 'النموذج المطلوب': 'model-original', 'رقم المحاولة': 2})
        self.assertEqual(records[1]['التفكير المطلوب'], 'عالٍ')
        self.assertEqual(records[1]['الحالة'], 'انتهى الدور؛ لم يُعتمد الدليل')
        self.assertEqual(snapshot, before)
        self.assertContains(response, 'التفكير المطلوب')

    def test_remaining_interface_values_and_recorded_narrative_are_localized(self):
        from .models import DispatcherHeartbeat
        from .views import _automation_evidence_value
        DispatcherHeartbeat.objects.create(key='local', state='running')
        self.job.result = 'UsageEvent completed; Turn7 medium'
        self.job.outcome_note = 'requested_observed_model_mismatch'
        self.job.save()
        self.client.force_login(self.supervisor)
        response = self.client.get('/sessions/automation/')
        self.assertContains(response, 'رموز المدخلات')
        self.assertContains(response, 'رموز المخرجات')
        self.assertContains(response, 'واقعة الاستخدام انتهى التنفيذ;')
        self.assertContains(response, 'دور التنفيذ7 متوسط')
        self.assertContains(response, 'النموذج المرصود يخالف المطلوب')
        self.assertEqual(response.context['heartbeats'][0]['state_label'], 'قيد التنفيذ')
        self.assertEqual(response.context['jobs'][0]['requested_thinking_label'], 'متوسط')
        self.assertEqual(_automation_evidence_value('required', False), 'لا')
        self.assertEqual(_automation_evidence_value('kind', 'review'), 'مراجعة')
        self.job.refresh_from_db()
        self.assertEqual(self.job.result, 'UsageEvent completed; Turn7 medium')
        self.assertEqual(self.job.outcome_note, 'requested_observed_model_mismatch')
