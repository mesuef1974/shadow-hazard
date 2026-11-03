"""Regression checks against a real SQLite file, not its shared-memory mode."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings


def make_source(path, count=100):
    with closing(sqlite3.connect(path)) as source, source:
        source.execute('CREATE TABLE usage_samples (sample_id INTEGER PRIMARY KEY, session_id TEXT, '
            'sampled_at TEXT, model TEXT, cumulative_total_tokens INTEGER, cumulative_input_tokens INTEGER, '
            'cumulative_cached_input_tokens INTEGER, cumulative_output_tokens INTEGER, '
            'cumulative_reasoning_output_tokens INTEGER, context_total_tokens INTEGER, '
            'context_window_tokens INTEGER, reasoning_effort TEXT, service_tier TEXT)')
        source.executemany('INSERT INTO usage_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(i,'contention-session','2026-11-03T10:00:00Z','model',i,i,0,0,0,20,100,'low','standard')
             for i in range(1,count+1)])


class SyncBatchTests(TestCase):
    def setUp(self):
        from workspace.models import Project
        from .models import TrackedSession
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'source.db'
        make_source(self.path)
        self.project=Project.objects.create(name='sync regression')
        TrackedSession.objects.create(project=self.project,external_id='contention-session',name='test')
        self.settings_override=override_settings(TOKEN_USAGE_SOURCE=self.path,LOCAL_DIR=Path(self.temp.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.context_patch=patch('token_usage.runtime_context.synchronize_context')
        self.context_patch.start()
        self.addCleanup(self.context_patch.stop)

    def test_unchanged_history_has_bounded_queries_and_metadata_updates_in_place(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .models import TokenSample, UsageSourceState
        from .services import synchronize
        self.assertEqual(synchronize().added,100)
        generation=UsageSourceState.objects.get().generation
        with CaptureQueriesContext(connection) as queries:
            result=synchronize()
        self.assertTrue(result.success)
        self.assertEqual(result.added,0)
        sample_queries=[q['sql'] for q in queries if 'token_usage_tokensample' in q['sql']]
        self.assertEqual(len(sample_queries),1)
        self.assertTrue(sample_queries[0].startswith('SELECT'))
        with closing(sqlite3.connect(self.path)) as source, source:
            source.execute("UPDATE usage_samples SET reasoning_effort='high',service_tier='priority'")
        self.assertEqual(synchronize().added,0)
        self.assertEqual(TokenSample.objects.filter(effort='high',tier='priority').count(),100)
        self.assertEqual(UsageSourceState.objects.get().generation,generation)

    def test_failed_commit_rolls_back_samples_and_generation_then_retry_succeeds(self):
        from django.db import DatabaseError
        from .models import TokenSample, UsageSourceState
        from .services import synchronize
        self.assertEqual(synchronize().added,100)
        original=UsageSourceState.objects.get()
        with closing(sqlite3.connect(self.path)) as source, source:
            source.execute('UPDATE usage_samples SET cumulative_total_tokens=999,cumulative_input_tokens=999 WHERE sample_id=1')
        with patch.object(UsageSourceState,'save',side_effect=DatabaseError('injected commit failure')):
            failed=synchronize()
        self.assertFalse(failed.success)
        self.assertEqual(failed.added,0)
        self.assertEqual(failed.error_code,'database_error')
        self.assertEqual(TokenSample.objects.count(),100)
        self.assertEqual(UsageSourceState.objects.get().generation,original.generation)
        retry=synchronize()
        self.assertTrue(retry.success)
        self.assertEqual(retry.added,100)
        self.assertNotEqual(UsageSourceState.objects.get().generation,original.generation)
        self.assertEqual(TokenSample.objects.count(),200)


class FileDatabaseContentionTests(SimpleTestCase):
    def test_reports_during_sync_preparation_commit_and_multiple_writers(self):
        # A separate process avoids changing Django's test-runner connection or
        # touching the live DB. Settings retain the production SQLite options.
        bootstrap = """
import sys
from pathlib import Path
from django.conf import settings
settings.DATABASES['default']['NAME']=str(Path(sys.argv[1])/'project.db')
settings.LOCAL_DIR=Path(sys.argv[1])
settings.TOKEN_USAGE_SOURCE=Path(sys.argv[1])/'source.db'
import django
django.setup()
from token_usage.test_sync_contention import exercise_contention
exercise_contention()
"""
        environment={**os.environ,'DJANGO_SETTINGS_MODULE':'config.settings'}
        environment.pop('DATABASE_URL',None)
        environment.pop('DJANGO_ENV',None)
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,'-c',bootstrap,folder],
                cwd=Path(__file__).resolve().parents[1],env=environment,
                capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('82 verified reports; one duplicate key; no lock errors',result.stdout)


def exercise_contention():
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from django.conf import settings
    from django.db import connection, connections
    from workspace.memory import document
    from workspace.models import Project, ProjectDocumentation
    from token_usage.capacity import verified_document
    from .models import TrackedSession, TokenSample, SyncRun, UsageSourceState, CollectorState
    from .services import synchronize, _source_sample

    with connection.schema_editor() as editor:
        for model in (Project,ProjectDocumentation,TrackedSession,TokenSample,SyncRun,UsageSourceState,CollectorState):
            editor.create_model(model)
    project=Project.objects.create(name='isolated concurrent writes')
    TrackedSession.objects.create(project=project,external_id='contention-session',name='test')
    make_source(settings.TOKEN_USAGE_SOURCE,1000)

    def sync():
        try:
            return synchronize()
        finally:
            connections.close_all()

    def report(key, attempted=None):
        def observe(execute,sql,params,many,context):
            if attempted and sql.startswith('BEGIN'):
                attempted.set()
            return execute(sql,params,many,context)
        try:
            with connection.execute_wrapper(observe):
                saved=document(project,'contention',key,'Concurrent save',{'ok':True})
            # Re-read after commit; a second identical call must not duplicate it.
            saved=ProjectDocumentation.objects.get(pk=saved.pk)
            assert verified_document(saved)
            return saved.pk
        finally:
            connections.close_all()

    entered=Event(); release=Event()
    def pause_preparation(row):
        if not entered.is_set():
            entered.set()
            assert release.wait(10),'preparation was never released'
        return _source_sample(row)

    try:
        with patch('token_usage.runtime_context.synchronize_context'), ThreadPoolExecutor(max_workers=8) as pool:
            with patch('token_usage.services._source_sample',side_effect=pause_preparation):
                syncing=pool.submit(sync)
                try:
                    assert entered.wait(5),'sync did not reach preparation'
                    pool.submit(report,'during-preparation').result(timeout=5)
                finally:
                    release.set()
                assert syncing.result(timeout=10).success

            # Force a second report to arrive while the sync owns the write lock.
            with closing(sqlite3.connect(settings.TOKEN_USAGE_SOURCE)) as source, source:
                source.execute('INSERT INTO usage_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (1001,'contention-session','2026-11-03T10:00:00Z','model',1001,1001,0,0,0,20,100,'low','standard'))
            entered.clear(); release.clear(); attempted=Event()
            original_bulk_create=TokenSample.objects.bulk_create
            def pause_commit(*args,**kwargs):
                result=original_bulk_create(*args,**kwargs)
                entered.set()
                assert release.wait(10),'commit was never released'
                return result
            with patch.object(TokenSample.objects,'bulk_create',side_effect=pause_commit):
                syncing=pool.submit(sync)
                try:
                    assert entered.wait(5),'sync did not reach commit'
                    saving=pool.submit(report,'during-commit',attempted)
                    assert attempted.wait(5),'report transaction did not start'
                    try:
                        saving.result(timeout=0.2)
                    except TimeoutError:
                        pass  # It must wait, not fail on a read-to-write upgrade.
                    else:
                        raise AssertionError('report did not wait for the write lock')
                finally:
                    release.set()
                assert syncing.result(timeout=10).success
                saving.result(timeout=10)

            futures=[pool.submit(report,f'parallel-{i}') for i in range(80)]
            for future in futures:
                future.result(timeout=10)
            duplicate_ids=[f.result(timeout=10) for f in
                [pool.submit(report,'parallel-0') for _ in range(8)]]
            assert len(set(duplicate_ids))==1
            try:
                document(project,'contention','parallel-0','Changed content',{'ok':True})
            except ValueError:
                pass
            else:
                raise AssertionError('conflicting content was accepted')
        assert ProjectDocumentation.objects.count()==82
        assert TokenSample.objects.count()==1001
        assert all(verified_document(row) for row in ProjectDocumentation.objects.all())
        print('82 verified reports; one duplicate key; no lock errors')
    finally:
        connections.close_all()
