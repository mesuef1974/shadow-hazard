import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import automation_live
from .models import DispatcherHeartbeat

URL = '/sessions/automation-model/live.json'


class AutomationLiveTests(TestCase):
    def setUp(self):
        automation_live.reset_cache()
        User = get_user_model()
        self.supervisor = User.objects.create_user(username='mesuef', role='supervisor')
        self.student = User.objects.create_user(username='saud', role='student')

    def test_live_status_is_supervisor_only_json_without_caching(self):
        self.assertEqual(self.client.get(URL).status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.client.force_login(self.supervisor)
        with patch.object(automation_live.shutil, 'which', return_value=None):
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        data = response.json()
        self.assertTrue(data['zero_tokens'])
        self.assertEqual(set(data) - {'generated_at', 'zero_tokens'},
                         {'events', 'git', 'docs', 'github', 'platform'})
        self.assertFalse(data['github']['available'])
        self.assertIn('gh', data['github']['reason'])

    def test_events_keep_latest_per_kind_and_skip_broken_lines(self):
        rows = [{'at': '2026-11-04T10:00:00+00:00', 'kind': 'commit', 'result': 'pass', 'detail': '3 ملف'},
                {'at': '2026-11-04T10:01:00+00:00', 'kind': 'commit', 'result': 'override', 'detail': 'سبب'},
                {'at': '2026-11-04T10:02:00+00:00', 'kind': 'push', 'result': 'fail', 'detail': 'x' * 500}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'events.jsonl')
            path.write_text('not json\n' + '\n'.join(json.dumps(r) for r in rows) + '\n{"kind": 1}\n',
                            encoding='utf-8')
            data = automation_live.events(path)
        self.assertEqual(data['latest']['commit']['result'], 'override')
        self.assertEqual(data['latest']['override']['detail'], 'سبب')
        self.assertEqual(len(data['latest']['push']['detail']), 200)
        self.assertEqual([r['kind'] for r in data['recent']], ['push', 'commit', 'commit'])

    def test_missing_event_log_is_an_empty_live_state(self):
        data = automation_live.events(Path(tempfile.gettempdir(), 'no-such-events.jsonl'))
        self.assertEqual((data['available'], data['latest'], data['recent']), (True, {}, []))

    def test_github_runs_are_limited_to_ci_and_cached(self):
        runs = [{'workflowName': 'CI', 'headBranch': 'feat/x', 'headSha': 'abcdef1234', 'status': 'completed',
                 'conclusion': 'success', 'event': 'push', 'createdAt': 'a', 'updatedAt': 'b', 'url': 'u'},
                {'workflowName': 'Dependabot Updates', 'headBranch': 'main', 'headSha': '1', 'status': 'queued'}]
        with patch.object(automation_live.shutil, 'which', return_value='gh'), \
                patch.object(automation_live, '_run', return_value=json.dumps(runs)) as run:
            first = automation_live.github_state()
            second = automation_live.github_state()
        self.assertEqual(run.call_count, 2)  # no CI run on main in the window: one extra, cached, question
        self.assertIs(first, second)
        self.assertEqual([r['sha'] for r in first['runs']], ['abcdef1'])

    def test_main_stays_visible_however_busy_the_branches_are(self):
        def ci(branch, sha, conclusion='success'):
            return {'workflowName': 'CI', 'headBranch': branch, 'headSha': sha, 'status': 'completed',
                    'conclusion': conclusion, 'event': 'push', 'createdAt': 'a', 'updatedAt': 'b', 'url': 'u'}
        busy = [ci(f'feat/{n}', f'{n}' * 7) for n in range(8)] + [ci('main', 'm' * 7, 'failure')]
        with patch.object(automation_live.shutil, 'which', return_value='gh'), \
                patch.object(automation_live, '_run', return_value=json.dumps(busy)) as run:
            runs = automation_live.github_state()['runs']
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(runs), 7)
        self.assertEqual((runs[-1]['headBranch'], runs[-1]['conclusion']), ('main', 'failure'))
        automation_live.reset_cache()
        outputs = iter([json.dumps(busy[:8]), json.dumps([busy[-1]])])
        with patch.object(automation_live.shutil, 'which', return_value='gh'), \
                patch.object(automation_live, '_run', side_effect=lambda *a, **k: next(outputs)) as run:
            runs = automation_live.github_state()['runs']
        self.assertIn('--branch', run.call_args_list[1].args[0])
        self.assertEqual(runs[-1]['headBranch'], 'main')

    def test_platform_heartbeat_freshness(self):
        DispatcherHeartbeat.objects.create(key='local', state='running', pid=1)
        stale = DispatcherHeartbeat.objects.create(key='readonly', state='running', pid=2)
        DispatcherHeartbeat.objects.filter(pk=stale.pk).update(updated_at=timezone.now() - timedelta(minutes=5))
        beats = {b['key']: b['online'] for b in automation_live.platform_state()['heartbeats']}
        self.assertEqual(beats, {'local': True, 'readonly': False})

    def test_one_broken_source_does_not_hide_the_others(self):
        with patch.object(automation_live, 'git_state', side_effect=OSError('git missing')), \
                patch.object(automation_live.shutil, 'which', return_value=None):
            data = automation_live.snapshot()
        self.assertFalse(data['git']['available'])
        self.assertIn('git missing', data['git']['reason'])
        self.assertTrue(data['platform']['available'])
