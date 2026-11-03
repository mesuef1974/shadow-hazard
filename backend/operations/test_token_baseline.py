import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

spec = importlib.util.spec_from_file_location('token_baseline',
    Path(__file__).resolve().parents[2] / 'tools' / 'measure-token-baseline.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class TokenBaselineTests(SimpleTestCase):
    def sample(self, directory, start=10, end=13):
        path = Path(directory) / 'evidence.jsonl'
        raw = '\n'.join(json.dumps({'payload': payload}) for payload in
            [{'started_at': start}, {'completed_at': end}]).encode()
        path.write_bytes(raw)
        job = SimpleNamespace(pk=1, state='completed', created_at=timezone.now(),
            charter=SimpleNamespace(key='review'), task_context=SimpleNamespace(kind='text'),
            session_dispatch=SimpleNamespace(attempt_no=2, escalation_count=1))
        data = {'job_id': 1, 'model': 'gpt-6-luna', 'thinking': 'low',
            'usage': {'input_tokens': 100, 'cached_input_tokens': 60,
                      'output_tokens': 10, 'total_tokens': 110},
            'evidence': {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                         'start_line': 1, 'complete_line': 2}}
        return job, data

    def run_sample(self, job, data):
        with patch.object(baseline.ModelJob.objects, 'select_related') as query, \
                patch.object(baseline, 'metrics', return_value=data):
            query.return_value.order_by.return_value = [job]
            return baseline.measure()

    def test_counts_cache_once_and_does_not_infer_quality(self):
        with TemporaryDirectory() as directory:
            report = self.run_sample(*self.sample(directory))
        row = report['rows'][0]
        self.assertEqual(row['wall_seconds'], 3)
        self.assertEqual(report['groups'][0]['total_tokens']['median'], 110)
        self.assertEqual(row['quality'], 'not_independently_scored')
        self.assertEqual(row['attempt_no'], 2)

    def test_changed_evidence_is_excluded_not_zero(self):
        with TemporaryDirectory() as directory:
            job, data = self.sample(directory)
            data['evidence']['sha256'] = 'changed'
            report = self.run_sample(job, data)
        self.assertEqual(report['rows'], [])
        self.assertEqual(len(report['excluded']), 1)
        self.assertEqual(report['groups'], [])

    def test_invalid_timestamps_are_excluded(self):
        for start, end in [(10, 9), (True, 13), (10, float('nan'))]:
            with self.subTest(start=start, end=end), TemporaryDirectory() as directory:
                report = self.run_sample(*self.sample(directory, start, end))
                self.assertEqual(report['rows'], [])
                self.assertEqual(len(report['excluded']), 1)

    def test_missing_requested_job_fails_closed(self):
        with patch.object(baseline.ModelJob.objects, 'select_related') as query:
            query.return_value.order_by.return_value.filter.return_value = []
            with self.assertRaisesRegex(ValueError, 'missing jobs'):
                baseline.measure([999])
