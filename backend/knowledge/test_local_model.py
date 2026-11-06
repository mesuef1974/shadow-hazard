from django.test import SimpleTestCase
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.core.exceptions import PermissionDenied
from .tests import KnowledgeSearchTests
from .local_model import answer, generate, _generation_lock, passages, readable
from .local_model import verify_answer as actual_verify_answer


class LocalModelTests(TestCase):
    def setUp(self):
        KnowledgeSearchTests.setUp(self)
        verifier = patch('knowledge.local_model.verify_answer', return_value=True)
        verifier.start()
        self.addCleanup(verifier.stop)

    @patch('knowledge.local_model.generate')
    def test_relevance_verifier_can_reject_exact_but_irrelevant_quote(self, model):
        model.side_effect = lambda sources, query: {'citations': [{'source_id': sources[0]['source_id'], 'passage_id': 1}]}
        with patch('knowledge.local_model.verify_answer', return_value=False):
            _, response = answer(self.student, self.project.pk, 'الحساس')
        self.assertEqual(response['status'], 'insufficient_evidence')
        self.assertEqual(response['citations'], [])

    def test_passage_offsets_preserve_original_and_only_add_spacing(self):
        text = 'الرسالة28والتسليمdispatch29completed؛\nإعدادات localhost8011'
        parts = passages(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            original = text[part['start']:part['end']]
            self.assertEqual(''.join(part['text'].split()), ''.join(original.split()))
        self.assertIn('dispatch 29 completed', readable(text))
        self.assertIn('docs/brand/approved-v1/tokens.css', readable('راجع docs/brand/approved-v1/tokens.css'))

    @patch('knowledge.local_model.completion')
    def test_verifier_requires_explicit_boolean_not_a_truthy_string(self, complete):
        for invalid in ({'supported': 'true'}, {}, [], {'supported': 1}):
            complete.return_value = invalid
            with self.assertRaises(ValueError):
                actual_verify_answer('سؤال', [])
        for invalid in ({'supported': True}, {'supported': True, 'answer': ''}):
            complete.return_value = invalid
            with self.assertRaises(ValueError):
                actual_verify_answer('سؤال', [])
        complete.return_value = {'supported': True, 'answer': 'لا؛ القاعدة تمنع ذلك.'}
        self.assertTrue(actual_verify_answer('سؤال', []))

    @patch('knowledge.local_model.generate')
    def test_revocation_during_evidence_verification_discards_answer(self, model):
        model.side_effect = lambda sources, query: {'citations': [{'source_id': sources[0]['source_id'], 'passage_id': 1}]}
        def revoke(query, citations):
            self.public.private = True
            self.public.save()
            return True
        with patch('knowledge.local_model.verify_answer', side_effect=revoke):
            results, response = answer(self.student, self.project.pk, 'الحساس')
        self.assertEqual(results, [])
        self.assertEqual(response['citations'], [])

    @patch('knowledge.local_model.generate')
    def test_passage_selection_maps_to_original_and_unknown_id_is_rejected(self, model):
        def select(sources, query):
            self.assertTrue(sources[0]['passages'])
            return {'citations': [{'source_id': sources[0]['source_id'], 'passage_id': 1}]}
        model.side_effect = select
        _, response = answer(self.student, self.project.pk, 'الحساس')
        self.assertEqual(response['citations'][0]['quote'], self.public.body)
        model.side_effect = lambda sources, query: {'citations': [{'source_id': sources[0]['source_id'], 'passage_id': 999}]}
        _, response = answer(self.student, self.project.pk, 'الحساس')
        self.assertEqual(response['status'], 'unverified_answer')

    @patch('knowledge.local_model.generate')
    def test_distinct_connection_validation_and_insufficiency_messages(self, model):
        states = [(TimeoutError(), 'connection_error'),
                  (ValueError('malformed JSON'), 'unverified_answer'),
                  ({'citations': []}, 'insufficient_evidence')]
        for value, expected in states:
            model.side_effect = value if isinstance(value, Exception) else None
            model.return_value = value
            with patch('knowledge.local_model.verify_answer', return_value=False):
                _, response = answer(self.student, self.project.pk, 'الحساس')
            self.assertEqual(response['status'], expected)

    @patch('knowledge.local_model.generate')
    def test_only_permitted_sources_reach_model(self, model):
        def respond(sources, query):
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]['text'], self.public.body)
            self.assertEqual(sources[0]['origin_id'], self.public.pk)
            self.assertEqual(sources[0]['kind'], 'decision')
            return {'citations': [{'source_id': sources[0]['source_id'], 'quote': self.public.body}]}
        model.side_effect = respond
        results, response = answer(self.student, self.project.pk, 'الحساس')
        self.assertEqual(len(response['citations']), 1)
        self.assertEqual(len(results), 1)

    @patch('knowledge.local_model.generate')
    def test_unknown_citations_and_fabricated_quotes_rejected(self, model):
        for citation in ({'source_id': 999999, 'quote': 'bad'},
                         {'source_id': self.public.knowledge_source.pk, 'quote': 'نص مختلق'}):
            model.return_value = {'citations': [citation]}
            _, response = answer(self.student, self.project.pk, 'الحساس')
            self.assertEqual(response['citations'], [])

    @patch('knowledge.local_model.generate')
    def test_permission_change_during_generation_discards_answer(self, model):
        def revoke(sources, query):
            self.public.private = True
            self.public.save()
            return {'citations': [{'source_id': sources[0]['source_id'], 'quote': self.public.body}]}
        model.side_effect = revoke
        results, response = answer(self.student, self.project.pk, 'الحساس')
        self.assertEqual(results, [])
        self.assertEqual(response['citations'], [])

    @patch('knowledge.local_model.generate')
    def test_user_deactivation_during_generation_denies_response(self, model):
        def revoke(sources, query):
            type(self.student).objects.filter(pk=self.student.pk).update(is_active=False)
            return {'citations': []}
        model.side_effect = revoke
        with self.assertRaises(PermissionDenied):
            answer(self.student, self.project.pk, 'الحساس')

    @patch('knowledge.local_model.generate', side_effect=TimeoutError)
    def test_timeout_preserves_search_and_empty_search_never_calls_model(self, model):
        results, response = answer(self.student, self.project.pk, 'الحساس')
        self.assertEqual(len(results), 1)
        self.assertEqual(response['citations'], [])
        model.reset_mock()
        answer(self.student, self.project.pk, 'لاوجودلهذا')
        model.assert_not_called()

    @patch('knowledge.local_model.generate')
    def test_busy_does_not_start_second_generation(self, model):
        _generation_lock.acquire()
        try:
            _, response = answer(self.student, self.project.pk, 'الحساس')
            self.assertIn('مشغول', response['message'])
            model.assert_not_called()
        finally:
            _generation_lock.release()

    @patch('knowledge.local_model.generate', return_value={'citations': []})
    def test_answer_endpoint_requires_login_post_and_csrf(self, model):
        url = '/knowledge/answer/'
        self.assertEqual(self.client.post(url, {'q': 'الحساس'}).status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch('knowledge.local_model.verify_answer', return_value=False):
            response = self.client.post(url, {'q': 'الحساس'})
        self.assertContains(response, 'لم يجد النموذج مقتطفًا')
        self.assertIn('no-store', response['Cache-Control'])
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.student)
        self.assertEqual(strict.post(url, {'q': 'الحساس'}).status_code, 403)

    @patch('knowledge.local_model.urllib.request.build_opener')
    def test_loopback_transport_and_response_format(self, build):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'content': '{"citations":[]}'}}],
        }).encode()
        build.return_value.open.return_value = response
        self.assertEqual(generate([], 'سؤال'), {'citations': []})
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:1234/v1/chat/completions')
        self.assertEqual(build.return_value.open.call_args.kwargs['timeout'], 35)


class ThinkingModelTests(SimpleTestCase):
    def test_last_message_disables_thinking_and_stays_a_copy(self):
        from knowledge.local_model import without_thinking
        messages = [{'role': 'system', 'content': 'a'}, {'role': 'user', 'content': 'b'}]
        self.assertEqual(without_thinking(messages)[-1]['content'], 'b /no_think')
        self.assertEqual(messages[-1]['content'], 'b')

    def test_empty_think_block_does_not_break_json(self):
        from knowledge.local_model import THINK
        self.assertEqual(THINK.sub('', '<think> </think>{"a":1}').strip(), '{"a":1}')
