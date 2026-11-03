import importlib.util
from pathlib import Path
from types import SimpleNamespace
from django.test import SimpleTestCase

spec = importlib.util.spec_from_file_location('equivalent_measurement',
    Path(__file__).resolve().parents[2] / 'tools' / 'measure-equivalent-tasks.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EquivalentQualityTests(SimpleTestCase):
    expected = {'A': 'PASS', 'B': 'BLOCKED', 'C': 'UNAVAILABLE'}

    def test_exact_result_scores_three(self):
        result = module.score('{"C":"UNAVAILABLE","B":"BLOCKED","A":"PASS"}', self.expected)
        self.assertTrue(result['exact_match'])
        self.assertEqual(result['correct_cases'], 3)

    def test_wrong_case_and_extra_keys_do_not_pass(self):
        result = module.score('{"A":"PASS","B":"PASS","C":"UNAVAILABLE"}', self.expected)
        self.assertEqual(result['correct_cases'], 2)
        self.assertFalse(result['exact_match'])
        result = module.score('{"A":"PASS","B":"BLOCKED","C":"UNAVAILABLE","extra":1}', self.expected)
        self.assertEqual(result['correct_cases'], 3)
        self.assertFalse(result['exact_match'])

    def test_duplicate_keys_and_prose_are_invalid(self):
        for text in ['{"A":"PASS","A":"BLOCKED"}', '```json\n{}\n```', 'Done: {}', '[]', 'null',
                     '{"A":NaN}', '{"A":Infinity}', '{"A":-Infinity}']:
            with self.subTest(text=text):
                result = module.score(text, self.expected)
                self.assertFalse(result['valid_json_object'])
                self.assertFalse(result['exact_match'])

    def test_changed_stored_result_is_not_scored(self):
        correct = '{"A":"PASS","B":"BLOCKED","C":"UNAVAILABLE"}'
        result = module.score_verified(correct, '{}', self.expected)
        self.assertFalse(result['source_match'])
        self.assertIsNone(result['correct_cases'])
        self.assertTrue(module.score_verified(correct, correct, self.expected)['exact_match'])

    def test_missing_dispatch_is_unknown_not_zero(self):
        result = module.dispatch_status(SimpleNamespace())
        self.assertIsNone(result['dispatch_state'])
        self.assertIsNone(result['recorded_turns'])
        self.assertIsNone(result['attempt_no'])
