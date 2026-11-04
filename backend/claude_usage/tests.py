import json
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.models import Project

from .models import AiModel, ClaudeMessage, ClaudeSession, GitBranch, ImportRun, SeatQuotaSnapshot
from .parser import parse_line, strip_line
from .services import import_usage, summary

REPO = '/repo/shz'


def line(mid, at, model='claude-opus-5-5', out=10, cwd=REPO, session='s1', branch='main', **extra):
    return json.dumps({'type': 'assistant', 'sessionId': session, 'timestamp': at.isoformat(), 'cwd': cwd,
                       'gitBranch': branch, 'message': {'id': mid, 'model': model, 'content': [{'type': 'text', 'text': 'سر'}],
                       'usage': {'input_tokens': 1, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000,
                                 'output_tokens': out}}, **extra}, ensure_ascii=False) + '\n'


class ParserTests(TestCase):
    def test_skips_other_lines_and_synthetic_and_bad_numbers(self):
        now = timezone.now()
        self.assertIsNone(parse_line('{"type":"user"}'))
        self.assertIsNone(parse_line('not json'))
        self.assertIsNone(parse_line(line('a', now, model='<synthetic>')))
        bad = json.loads(line('b', now)); bad['message']['usage']['output_tokens'] = -1
        self.assertIsNone(parse_line(json.dumps(bad)))

    def test_strip_keeps_numbers_only(self):
        stripped = strip_line(line('a', timezone.now()))
        self.assertNotIn('سر', stripped)
        self.assertEqual(parse_line(stripped).surface, 'cloud')
        self.assertEqual(parse_line(stripped).usage['output_tokens'], 10)


class ImportTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.project = Project.objects.create(name='ظل الخطر')
        self.now = timezone.now()

    def run_import(self):
        return import_usage([('claude_projects', self.dir, True)])

    def write(self, name, text, mode='a'):
        with (self.dir / name).open(mode, encoding='utf-8', newline='') as handle:
            handle.write(text)

    @override_settings(BASE_DIR=Path(REPO) / 'backend')
    def test_dedupes_streaming_lines_and_normalizes(self):
        self.write('x.jsonl', line('m1', self.now, out=3) + line('m1', self.now, out=9) + line('m2', self.now, branch='main'))
        run = self.run_import()
        self.assertTrue(run.ok)
        self.assertEqual(ClaudeMessage.objects.count(), 2)
        self.assertEqual(ClaudeMessage.objects.get(message_id='m1').output_tokens, 9)
        self.assertEqual(AiModel.objects.count(), 1)
        self.assertEqual(AiModel.objects.get().family, 'Opus')
        self.assertEqual(GitBranch.objects.count(), 1)
        self.assertEqual(ClaudeSession.objects.get().project, self.project)

    @override_settings(BASE_DIR=Path(REPO) / 'backend')
    def test_other_repositories_are_skipped(self):
        self.write('x.jsonl', line('m1', self.now, cwd='/other/project') + line('m2', self.now, cwd=REPO + '/backend'))
        run = self.run_import()
        self.assertEqual(run.skipped_lines, 1)
        self.assertEqual(list(ClaudeMessage.objects.values_list('message_id', flat=True)), ['m2'])

    @override_settings(BASE_DIR=Path(REPO) / 'backend')
    def test_incremental_partial_lines_and_no_empty_runs(self):
        first = line('m1', self.now)
        self.write('x.jsonl', first + line('m2', self.now)[:-5])  # second line not finished yet
        self.run_import()
        self.assertEqual(ClaudeMessage.objects.count(), 1)
        self.write('x.jsonl', line('m2', self.now)[-5:])
        self.assertEqual(self.run_import().messages_new, 1)
        runs = ImportRun.objects.count()
        self.assertEqual(self.run_import().files_read, 0)
        self.assertEqual(ImportRun.objects.count(), runs)
        self.write('x.jsonl', line('m3', self.now), mode='w')  # file replaced and shorter
        self.assertEqual(self.run_import().messages_new, 1)

    def test_exported_files_need_no_repo_filter(self):
        self.write('cloud.jsonl', strip_line(line('c1', self.now, cwd='/home/claude')) + '\n')
        import_usage([('session_export', self.dir, False)])
        self.assertEqual(ClaudeSession.objects.get().surface, 'cloud')


class SummaryAndPageTests(TestCase):
    def setUp(self):
        now = timezone.now()
        opus = AiModel.objects.create(name='claude-opus-5-5', family='Opus')
        haiku = AiModel.objects.create(name='claude-haiku-5-5', family='Haiku')
        old = ClaudeSession.objects.create(session_id='old')
        self.current = ClaudeSession.objects.create(session_id='current', surface='cloud')
        ClaudeMessage.objects.create(message_id='a', session=old, model=haiku, at=now - timedelta(days=8), output_tokens=5)
        ClaudeMessage.objects.create(message_id='b', session=self.current, model=opus, at=now - timedelta(hours=1), output_tokens=7,
                                     cache_creation_input_tokens=3, input_tokens=1, cache_read_input_tokens=50)
        self.owner = get_user_model().objects.create_user(username='mesuef', role='supervisor')

    def test_windows_and_session(self):
        data = summary()
        week, month = data['windows']
        self.assertEqual(week['totals']['output_tokens'], 7)
        self.assertEqual(month['totals']['output_tokens'], 12)
        self.assertEqual(week['totals']['fresh'], 11)
        self.assertEqual(data['session']['session'], self.current)
        self.assertEqual([row['model__name'] for row in month['models']], ['claude-opus-5-5', 'claude-haiku-5-5'])

    def test_page_for_supervisor_only(self):
        self.client.force_login(self.owner)
        page = self.client.get('/claude-usage/')
        self.assertContains(page, 'claude-opus-5-5')
        self.assertContains(page, 'آخر 7 أيام')
        student = get_user_model().objects.create_user(username='saud', role='student')
        self.client.force_login(student)
        self.assertEqual(self.client.get('/claude-usage/').status_code, 403)

    def test_quota_entry(self):
        self.client.force_login(self.owner)
        self.client.post('/claude-usage/quota/', {'weekly_percent': '42٫5', 'session_percent': ''})
        self.assertEqual(str(SeatQuotaSnapshot.objects.get().weekly_percent), '42.5')
        response = self.client.post('/claude-usage/quota/', {'weekly_percent': '140'})
        self.assertIn('quota_error=1', response['Location'])
        self.assertEqual(SeatQuotaSnapshot.objects.count(), 1)
