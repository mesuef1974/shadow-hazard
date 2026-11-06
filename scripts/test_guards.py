"""اختبارات الحرّاس (مهمة 0002): تعمل بـ unittest وحده دون Django."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automation_events
import guard_frozen
import prepush_check
import session_start

GUARD = Path(guard_frozen.__file__).resolve()


def write(root, relative, text='x\n'):
    path = Path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


class RepoCase(unittest.TestCase):
    """مستودع مؤقت معزول عن إعدادات Git العامة (توقيع، محرر...) ويُحذف بعد الاختبار."""

    def make_dir(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def make_repo(self):
        root = self.make_dir()
        config = write(self.make_dir(), 'gitconfig', '')
        # متغيرات GIT_* يضبطها خطّاف pre-push (GIT_DIR وGIT_INDEX_FILE...)؛ نسخها كانت توجّه أوامر
        # المستودع المؤقت إلى المستودع الحقيقي فتخرّب فهرسه وإعداده (2026-11-05).
        self.env = {k: v for k, v in os.environ.items()
                    if k not in (guard_frozen.OVERRIDE, 'GITHUB_ACTIONS') and not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
                        GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
        # الدوال المستدعاة داخل العملية (prepush_check) تقرأ os.environ لا self.env، فتُنظَّف هي أيضًا.
        isolated = patch.dict(os.environ)
        isolated.start()
        self.addCleanup(isolated.stop)
        for key in [k for k in os.environ if k.startswith('GIT_')]:
            del os.environ[key]
        self.git(root, 'init', '-q')
        return root

    def git(self, root, *args):
        return subprocess.run(['git', *args], cwd=root, env=self.env, check=True,
                              capture_output=True).stdout.decode('utf-8').strip()


class FrozenPathTests(unittest.TestCase):
    def test_nothing_is_frozen_after_adr_0006(self):
        for path in ['docs/brand/index.html', 'backend/workspace/views.py',
                     'docs/blueprint/automation-map.js']:
            self.assertFalse(guard_frozen.frozen(path), path)


class GuardHookTests(RepoCase):
    def setUp(self):
        self.root = self.make_repo()
        write(self.root, 'README.md')
        write(self.root, 'docs/brand/tokens.css')
        self.git(self.root, 'add', '.')
        self.git(self.root, 'commit', '-q', '-m', 'base')

    def guard(self, *args, reason=None):
        env = dict(self.env)
        if reason is not None:
            env[guard_frozen.OVERRIDE] = reason
        return subprocess.run([sys.executable, str(GUARD), *args], cwd=self.root, env=env,
                              capture_output=True, text=True, encoding='utf-8')


    def test_open_zones_pass_silently(self):
        write(self.root, 'backend/simulation/tests.py')
        write(self.root, 'src/shadow_hazard/cli/__init__.py')
        self.git(self.root, 'add', '.')
        result = self.guard()
        self.assertEqual((result.returncode, result.stderr), (0, ''))





class AutomationEventsTests(RepoCase):
    def events(self, root):
        text = Path(root, automation_events.FILE).read_text(encoding='utf-8')
        return [json.loads(line) for line in text.splitlines()]

    def test_record_appends_bounded_json_and_skips_ci(self):
        root = self.make_dir()
        with patch.dict(os.environ):
            os.environ.pop('GITHUB_ACTIONS', None)
            automation_events.record('push', 'pass', 'x' * 300, root)
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            automation_events.record('push', 'fail', '', root)
        rows = self.events(root)
        self.assertEqual([(r['kind'], r['result']) for r in rows], [('push', 'pass')])
        self.assertEqual(len(rows[0]['detail']), 200)

    def test_large_log_is_trimmed(self):
        root = self.make_dir()
        path = write(root, automation_events.FILE, '{"kind": "old"}\n' * 20000)
        self.assertGreater(path.stat().st_size, automation_events.MAX_BYTES)
        with patch.dict(os.environ):
            os.environ.pop('GITHUB_ACTIONS', None)
            automation_events.record('session', 'pass', '', root)
        self.assertEqual(len(self.events(root)), automation_events.KEEP_LINES + 1)


class PrepushHistoryTests(RepoCase):
    def test_tracked_local_files_are_reported(self):
        root = self.make_repo()
        write(root, 'README.md')
        write(root, 'backend/.local/db.sqlite3')
        self.git(root, 'add', 'README.md')
        self.assertEqual(prepush_check.tracked_private(root), [])
        self.git(root, 'add', '-f', 'backend/.local/db.sqlite3')
        self.assertEqual(prepush_check.tracked_private(root), ['backend/.local/db.sqlite3'])



class SessionStartTests(RepoCase):
    def setUp(self):
        self.root = self.make_dir()
        bullets = '\n'.join(f'- سطر حالة {i}' for i in range(20))
        write(self.root, 'docs/handoff-2026-11-04.md',
              f'# ملخص\n\n## القرارات\n\n- قرار\n\n## حالة الكود\n\n{bullets}\n\n'
              '## بعدها\n\n- لا\n')
        write(self.root, 'docs/handoff-template.md', '## حالة الكود\n\n- قالب\n')
        write(self.root, 'docs/tasks/0001-a.md',
              '\ufeff# مهمة 0001: أ\n- الحالة: جاهزة للتنفيذ؛ تفاصيل\n')
        write(self.root, 'docs/tasks/0002-b.md', '# مهمة 0002: ب\n- الحالة: منجزة في 2026-11-04\n')

    def test_output_is_bounded_and_lists_only_open_tasks(self):
        lines = session_start.render(self.root)
        self.assertLessEqual(len(lines), session_start.LIMIT)
        text = '\n'.join(lines)
        self.assertIn('\n- مهمة 0001: أ — جاهزة للتنفيذ', text)
        self.assertNotIn('0002', text)
        self.assertNotIn('قالب', text)
        self.assertIn('البقية في ملف التسليم', text)

    def test_short_status_is_printed_whole(self):
        write(self.root, 'docs/handoff-2026-11-04.md', '\ufeff## حالة الكود\n\n- واحد\n- اثنان\n')
        lines = session_start.render(self.root)
        self.assertEqual(lines[1:3], ['- واحد', '- اثنان'])

    def test_many_open_tasks_keep_status_and_mark_every_cut(self):
        for i in range(3, 25):
            write(self.root, f'docs/tasks/{i:04d}-x.md', f'# مهمة {i:04d}: س\n- الحالة: جاهزة\n')
        lines = session_start.render(self.root)
        self.assertEqual(len(lines), session_start.LIMIT)
        self.assertEqual(lines[1], '- سطر حالة 0')
        self.assertTrue(lines[-1].startswith('- …'))


if __name__ == '__main__':
    unittest.main()
