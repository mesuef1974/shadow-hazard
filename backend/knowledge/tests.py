from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from workspace.models import Project, ProjectMemory, ProjectDocumentation
from .models import KnowledgeSource, KnowledgeChunk
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from workspace.models import Decision
from workspace.memory import document
from .services import index_project, search


class KnowledgeSchemaTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Synthetic project')
        self.memory = ProjectMemory.objects.create(
            project=self.project, key='test', instruction='Synthetic instruction',
        )
        self.defaults = dict(
            title='Synthetic source', source_digest='a' * 64,
            source_updated_at=timezone.now(),
        )

    def test_origin_required_and_exclusive(self):
        documentation = ProjectDocumentation.objects.create(
            project=self.project, session_key='test', task_key='test',
            summary='Synthetic summary', evidence={}, digest='b' * 64,
        )
        for origins in ({}, {'memory': self.memory, 'documentation': documentation}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                KnowledgeSource.objects.create(**self.defaults, **origins)

    def test_defaults_and_project_follow_origin(self):
        source = KnowledgeSource.objects.create(memory=self.memory, **self.defaults)
        self.assertFalse(source.enabled)
        self.assertEqual(source.project_id, self.project.pk)

    def test_chunk_positions_unique_and_origin_deletion_cascades(self):
        source = KnowledgeSource.objects.create(memory=self.memory, **self.defaults)
        KnowledgeChunk.objects.create(source=source, position=0, text='Synthetic', digest='c' * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            KnowledgeChunk.objects.create(source=source, position=0, text='Duplicate', digest='d' * 64)
        self.memory.delete()
        self.assertFalse(KnowledgeSource.objects.exists())
        self.assertFalse(KnowledgeChunk.objects.exists())


class KnowledgeSearchTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Synthetic project')
        users = get_user_model().objects
        self.owner = users.create_user(username='mesuef', role='supervisor')
        self.student = users.create_user(username='saud', role='student')
        self.public = Decision.objects.create(project=self.project, title='معايرة الحساس',
            body='إجراء اختبار اصطناعي للحساس', author=self.owner)
        self.private = Decision.objects.create(project=self.project, title='قرار خاص',
            body='محتوى سري للحساس', private=True, author=self.owner)
        self.memory = ProjectMemory.objects.create(project=self.project, key='context.test',
            instruction='تعليمات داخلية للحساس')
        self.doc = document(self.project, 'test', 'selected', 'ملخص للحساس', {'secret': 'NEVER INDEX'})
        index_project(self.project, [self.doc.pk])

    def test_students_only_get_public_decisions(self):
        hits = search(self.student, self.project.pk, 'الحساس')
        self.assertEqual([(h['kind'], h['origin_id']) for h in hits], [('decision', self.public.pk)])
        self.assertEqual(len(search(self.owner, self.project.pk, 'الحساس')), 4)

    def test_revocation_and_stale_content_fail_closed(self):
        self.public.private = True
        self.public.save()
        self.assertEqual(search(self.student, self.project.pk, 'الحساس'), [])
        self.memory.instruction = 'محتوى جديد'
        self.memory.save()
        self.assertFalse(any(h['kind'] == 'memory' for h in search(self.owner, self.project.pk, 'الحساس')))
        index_project(self.project)
        self.assertTrue(any(h['kind'] == 'memory' for h in search(self.owner, self.project.pk, 'جديد')))

    def test_project_scope_and_unselected_documents(self):
        other = Project.objects.create(name='Other synthetic project')
        self.assertEqual(search(self.owner, other.pk, 'الحساس'), [])
        document(self.project, 'test', 'unselected', 'مستند مخفي', {'test': True})
        index_project(self.project)
        self.assertEqual(search(self.owner, self.project.pk, 'مخفي'), [])
        self.assertEqual(search(self.owner, self.project.pk, 'NEVER INDEX'), [])

    def test_arabic_normalization_exact_ids_and_empty_queries(self):
        self.assertTrue(search(self.student, self.project.pk, 'اِختبار'))
        hits = search(self.student, self.project.pk, str(self.public.pk))
        self.assertEqual(hits[0]['origin_id'], self.public.pk)
        self.assertEqual(search(self.owner, self.project.pk, '999999999'), [])
        self.assertEqual(search(self.owner, self.project.pk, '  '), [])

    def test_joined_ids_and_explicit_numeric_alternatives(self):
        self.public.body = 'المنفذ8011والتسليمdispatch29completed'
        self.public.save()
        index_project(self.project)
        self.assertTrue(search(self.student, self.project.pk, 'التسليم 29'))
        self.assertTrue(search(self.student, self.project.pk, 'المنفذ 8000 أم 8011'))
        self.assertEqual(search(self.student, self.project.pk, 'المنفذ 8000 و8011'), [])
        self.assertEqual(search(self.student, self.project.pk, 'المنفذ 801'), [])

    def test_arabic_definite_article_pronouns_and_dual_variants(self):
        self.public.body = 'المهارة والخدمة بنسختها؛ يرى الطالبان أعمالهما'
        self.public.save()
        index_project(self.project)
        for query in ('مهارة', 'الخدمة', 'الطالب', 'أعمال'):
            self.assertTrue(search(self.student, self.project.pk, query), query)
        other = Project.objects.create(name='Other')
        self.assertEqual(search(self.student, other.pk, 'مهارة'), [])

    def test_refresh_is_idempotent_and_invalid_document_is_hidden(self):
        before = (KnowledgeSource.objects.count(), KnowledgeChunk.objects.count())
        index_project(self.project)
        self.assertEqual(before, (KnowledgeSource.objects.count(), KnowledgeChunk.objects.count()))
        self.doc.summary = 'معدل للحساس'
        self.doc.save()
        self.assertFalse(any(h['kind'] == 'documentation' for h in search(self.owner, self.project.pk, 'الحساس')))

    def test_anonymous_inactive_and_invalid_selection(self):
        with self.assertRaises(PermissionDenied):
            search(AnonymousUser(), self.project.pk, 'الحساس')
        self.student.is_active = False
        with self.assertRaises(PermissionDenied):
            search(self.student, self.project.pk, 'الحساس')
        with self.assertRaises(ValueError):
            index_project(self.project, [999999999])

    def test_http_auth_permissions_and_limits(self):
        url = '/knowledge/search/'
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.student)
        data = self.client.get(url, {'q': 'الحساس'}).json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['origin_id'], self.public.pk)
        self.assertEqual(self.client.get(url, {'q': 'x' * 501}).status_code, 400)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_search_page_states_and_source_dates(self):
        url = '/knowledge/'
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.owner)
        initial = self.client.get(url)
        self.assertContains(initial, 'ابدأ بكلمة أو رقم قرار')
        self.assertContains(initial, 'dir="rtl"')
        self.assertContains(initial, 'maxlength="500"')
        response = self.client.get(url, {'q': 'الحساس'})
        self.assertContains(response, 'معايرة الحساس')
        self.assertContains(response, 'سجل التوثيق')
        self.assertContains(response, 'تاريخ المصدر')
        self.assertContains(response, '<time datetime=')
        self.assertContains(response, 'بتوقيت قطر')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(self.client.get(url, {'q': 'لاوجودلهذاالنص'}), 'لم نجد نتائج متاحة')
        error = self.client.get(url, {'q': 'x' * 501})
        self.assertContains(error, 'السؤال يتجاوز 500 حرف', status_code=400)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_search_page_hides_private_sources_and_revoked_results(self):
        self.client.force_login(self.student)
        response = self.client.get('/knowledge/', {'q': 'الحساس'})
        self.assertContains(response, 'معايرة الحساس')
        self.assertNotContains(response, 'قرار خاص')
        self.assertNotContains(response, 'محتوى سري')
        self.assertNotContains(response, 'تعليمات داخلية')
        self.assertNotContains(response, 'ملخص للحساس')
        self.assertEqual(len(response.context['results']), 1)
        self.public.private = True
        self.public.save()
        revoked = self.client.get('/knowledge/', {'q': 'الحساس'})
        self.assertContains(revoked, 'لم نجد نتائج متاحة')
        self.assertNotContains(revoked, 'معايرة الحساس')

    def test_full_documentation_page_is_supervisor_only_and_shows_verified_evidence(self):
        url = f'/knowledge/documentation/{self.doc.pk}/'
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.owner)
        results = self.client.get('/knowledge/', {'q': 'الحساس'})
        self.assertContains(results, 'عرض سجل التوثيق كاملًا')
        detail = self.client.get(url)
        self.assertContains(detail, 'الإطار البحثي الكامل')
        self.assertContains(detail, 'NEVER INDEX')
        self.assertContains(detail, 'selected')
        self.assertIn('no-store', detail['Cache-Control'])

    def test_full_documentation_page_rejects_unindexed_or_changed_sources(self):
        self.client.force_login(self.owner)
        unselected = document(self.project, 'test', 'not-selected', 'ملخص آخر', {'draft': True})
        self.assertEqual(self.client.get(f'/knowledge/documentation/{unselected.pk}/').status_code, 404)
        self.doc.summary = 'محتوى تغيّر بعد الفهرسة'
        self.doc.save()
        self.assertEqual(self.client.get(f'/knowledge/documentation/{self.doc.pk}/').status_code, 404)

    def test_research_protocol_page_uses_readable_arabic_sections(self):
        protocol = document(self.project, 'test', 'research-protocol', 'إطار بحث اصطناعي', {
            'status': 'research_protocol_v1',
            'question': 'ما أثر دمج مصدرين على الكشف؟',
            'hypotheses': ['قد تتحسن الحساسية عند حجب أحد المصدرين.'],
            'design': {'factors': {
                'visibility': ['clear', 'partial_bus_occlusion', 'full_occlusion'],
                'source_state': ['single', 'two_calibrated', 'stale_one', 'disagreeing'],
                'crossing': ['conflict', 'no_conflict'],
                'distance_time_to_conflict_s': ['prespecified bins'],
            }},
        })
        index_project(self.project, [protocol.pk])
        self.client.force_login(self.owner)
        response = self.client.get(f'/knowledge/documentation/{protocol.pk}/')
        self.assertContains(response, 'سؤال البحث')
        self.assertContains(response, 'مصدران معايران')
        self.assertContains(response, 'زمن أو مسافة الوصول إلى التعارض')
        self.assertNotContains(response, '"status"')
        self.assertNotContains(response, '<pre class="knowledge-evidence"')

    def test_revised_protocol_displays_registered_skill_review_without_claiming_installation(self):
        revision = document(self.project, 'test', 'research-protocol-v2', 'إطار بحث منقح', {
            'status': 'research_protocol_v2',
            'question': 'سؤال تجريبي قابل للقياس',
            'contribution': 'مساهمة ضمن المحاكاة فقط.',
            'literature_gate': 'لا ادعاء جدة قبل مراجعة الأدبيات.',
            'primary_contrast': 'مقارنة مصدر واحد بمصدرين على المشاهد المقترنة.',
            'hypotheses': ['فرضية قابلة للدحض.'],
            'design': {'factors': {}},
            'primary_outcomes': ['حساسية الكشف.'],
            'secondary_outcomes': ['زمن الإنذار.'],
            'analysis_plan': ['تحليل مقترن وفاصل ثقة.'],
            'uncertainty_plan': ['توثيق المعايرة والوحدات.'],
            'communication_plan': ['عرض الفرق مع فاصل الثقة.'],
            'peer_review_gate': ['مراجع مستقل يتحقق من الحسابات.'],
        })
        index_project(self.project, [revision.pk])
        self.client.force_login(self.owner)
        response = self.client.get(f'/knowledge/documentation/{revision.pk}/')
        for text in ('الفجوة والإضافة العلمية', 'المقارنة الأساسية المحددة مسبقًا',
                     'المعايرة وعدم اليقين', 'الرسوم والتقرير العلمي', 'بوابة مراجعة الأقران',
                     'المهارات في الإيصال 481 ترشيحات غير مثبتة'):
            self.assertContains(response, text)
        self.assertNotContains(response, 'research_protocol_v2')

    def test_student_research_plan_uses_direct_project_language(self):
        plan = document(self.project, 'test', 'student-research-v3', 'خطة مبسطة للطلاب', {
            'status': 'student_research_v3',
            'revision_of': 482,
            'question': 'هل يساعدنا مصدران افتراضيان؟',
            'audience': 'teenage students beginning to explore research',
            'skills_status': 'recommendations only; not installed or tested',
        })
        index_project(self.project, [plan.pk])
        self.client.force_login(self.owner)
        response = self.client.get(f'/knowledge/documentation/{plan.pk}/')
        self.assertContains(response, 'مقترح دراسة: ملاحظة المشاة المحجوبين في مشاهد المحاكاة')
        self.assertContains(response, 'تفاصيل الدراسة')
        self.assertContains(response, 'طريقة المقارنة')
        self.assertContains(response, 'ماذا نسجل؟')
        self.assertContains(response, 'كل التجارب داخل المحاكاة')
        self.assertNotContains(response, 'تحليل القدرة الإحصائية')
        self.assertNotContains(response, 'نسخة مبسطة')
        self.assertNotContains(response, 'رحلة البحث العلمي')
        self.assertNotContains(response, 'نريد أن نتعلم كيف نبحث')
        self.assertNotContains(response, 'student-research-journey-v3')
        self.assertNotContains(response, 'coordination')
        self.assertContains(response, 'العودة إلى البحث')
        self.assertNotContains(response, 'research_protocol_v3')
        search_response = self.client.get('/knowledge/', {'q': 'مشاة محجوبين'})
        self.assertContains(search_response, 'مقترح دراسة: ملاحظة المشاة المحجوبين في مشاهد المحاكاة')
        self.assertNotContains(search_response, 'خطة مبسطة للطلاب')

    def test_student_research_proposal_includes_miniature_product_and_simulation(self):
        plan = document(self.project, 'test', 'student-research-v4', 'مقترح مجسم ومحاكاة', {
            'status': 'student_research_v4',
            'revision_of': 484,
            'product': 'a tabletop bus and pedestrian model with a visual alert',
            'simulation': 'retain the digital scene for repeated comparisons',
        })
        index_project(self.project, [plan.pk])
        self.client.force_login(self.owner)
        response = self.client.get(f'/knowledge/documentation/{plan.pk}/')
        for text in ('المنتج النهائي المقترح', 'مجسم مكتبي تفاعلي', 'مصدر رصد واحد أو مصدران',
                     'مشهد محاكاة رقمية', 'على المجسم المكتبي', 'المجسم ثابت على طاولة'):
            self.assertContains(response, text)
        self.assertContains(response, 'لا تثبت سلامة النظام في طريق حقيقي')
        self.assertNotContains(response, 'خطة مبسطة')
        self.assertNotContains(response, 'رحلة البحث العلمي')

    def test_search_page_escapes_query_and_source_content(self):
        self.public.body = '<script>alert("الحساس")</script>'
        self.public.save()
        index_project(self.project)
        self.client.force_login(self.student)
        response = self.client.get('/knowledge/', {'q': '<script>الحساس</script>'})
        self.assertNotContains(response, '<script>alert(')
        self.assertContains(response, '&lt;script&gt;')
