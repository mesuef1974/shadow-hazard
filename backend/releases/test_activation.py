import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, OperationalError
from .tests import ReleaseTests
from .services import create_release, merge_release
from .models import Release, MergeAttempt, RepositoryLock
from .git import LocalGit
from .adapters import SimulatedTransport
from .factories import transport_factory


class GitFixture:
    def __init__(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'integration.git'
        subprocess.run(['git', 'init', '--bare', str(self.path)], check=True, capture_output=True)
        tree = self.git('mktree', input='')
        self.base = self.git('commit-tree', tree, '-m', 'base')
        self.source = self.git('commit-tree', tree, '-p', self.base, '-m', 'feature')
        self.divergent = self.git('commit-tree', tree, '-p', self.base, '-m', 'divergent')
        self.git('update-ref', 'refs/heads/main', self.base)
        self.git('update-ref', 'refs/heads/feature', self.source)

    def git(self, *args, input=None):
        return subprocess.run(['git', '-C', str(self.path), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
            input=input, capture_output=True, text=True, check=True, timeout=10).stdout.strip()


class ActivationTests(TestCase):
    setUp = ReleaseTests.setUp
    action = ReleaseTests.action
    approve = ReleaseTests.approve

    def candidate(self, fixture, ready=True):
        r = create_release(actor=self.writer, task_id=self.task.pk, commit=fixture.source, environment='staging',
            repository='isolated', source_ref='refs/heads/feature', target_branch='refs/heads/main', target_commit=fixture.base,
            manifest=dict.fromkeys(['artifact_sha256','migration_plan_sha256','settings_sha256'], 'b'*64))
        self.action(r, 'pin', self.writer)
        if ready:
            for kind in ['tests','migration','settings']:
                self.action(r, 'evidence', kind=kind, reference='test:ok', accepted=True)
            self.action(r, 'review', self.reviewer, accepted=True)
        return r

    def fixture(self):
        f = GitFixture(); self.addCleanup(f.temp.cleanup)
        return f

    def test_merge_endpoint_success_duplicate_and_detail(self):
        f=self.fixture(); r=self.candidate(f)
        self.client.force_login(self.owner)
        with override_settings(RELEASE_GIT_REPOSITORIES={'isolated':str(f.path)}):
            response=self.client.post(f'/releases/{r.pk}/merge/', json.dumps({'key':'merge'}), content_type='application/json')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['state'],'merged')
            again=self.client.post(f'/releases/{r.pk}/merge/', json.dumps({'key':'merge'}), content_type='application/json')
            self.assertEqual(again.json(),response.json())
        self.assertEqual(f.git('rev-parse','refs/heads/main'),f.source)
        self.assertEqual(MergeAttempt.objects.count(),1)
        self.assertEqual(self.client.get(f'/releases/{r.pk}/').json()['release']['target_commit'],f.base)

    def test_merge_gates_and_no_record_only_bypass(self):
        f=self.fixture(); r=self.candidate(f,False)
        with self.assertRaises(PermissionDenied): merge_release(r.pk,actor=self.writer,key='student',adapter=LocalGit(f.path))
        with self.assertRaises(ValidationError): merge_release(r.pk,actor=self.owner,key='missing',adapter=LocalGit(f.path))
        with self.assertRaises(ValidationError): self.action(r,'merge')
        self.assertEqual(f.git('rev-parse','refs/heads/main'),f.base)
        self.assertEqual(MergeAttempt.objects.count(),0)

    def test_divergence_and_pin_mismatch(self):
        for mismatch in [False,True]:
            f=self.fixture(); r=self.candidate(f)
            if mismatch: f.git('update-ref','refs/heads/feature',f.divergent)
            else: f.git('update-ref','refs/heads/main',f.divergent)
            result=merge_release(r.pk,actor=self.owner,key=f'merge-{mismatch}',adapter=LocalGit(f.path))
            self.assertEqual(result.state,'merge_failed')
            self.assertNotEqual(f.git('rev-parse','refs/heads/main'),f.source)

    def test_non_fast_forward_is_known_failure(self):
        f=self.fixture(); f.base=f.divergent; f.git('update-ref','refs/heads/main',f.base)
        r=self.candidate(f)
        self.assertEqual(merge_release(r.pk,actor=self.owner,key='divergent',adapter=LocalGit(f.path)).state,'merge_failed')

    def test_merge_unknown_never_reexecutes(self):
        f=self.fixture(); r=self.candidate(f)
        class Interrupted:
            calls=0
            def merge(self, release):
                self.calls+=1
                raise TimeoutError()
        adapter=Interrupted()
        self.assertEqual(merge_release(r.pk,actor=self.owner,key='unknown',adapter=adapter).state,'unknown')
        merge_release(r.pk,actor=self.owner,key='unknown',adapter=adapter)
        with self.assertRaises(ValidationError): merge_release(r.pk,actor=self.owner,key='retry',adapter=adapter)
        self.assertEqual(adapter.calls,1)
        self.assertIsNotNone(RepositoryLock.objects.get(repository='isolated').attempt_id)

    def test_deploy_endpoint_full_activation_and_rollback(self):
        f=self.fixture(); old=self.candidate(f)
        merge_release(old.pk,actor=self.owner,key='old-merge',adapter=LocalGit(f.path))
        self.approve(old)
        self.client.force_login(self.owner)
        adapter=SimulatedTransport()
        with override_settings(RELEASE_TRANSPORT_FACTORY=lambda:adapter):
            url=f'/releases/{old.pk}/deployments/'
            body=json.dumps({'key':'deploy','backup_id':self.backup.pk})
            self.assertEqual(self.client.post(url,body,content_type='application/json').json()['state'],'healthy')
            self.assertEqual(self.client.post(url,body,content_type='application/json').json()['state'],'healthy')
            self.assertEqual(adapter.calls,1)
            self.approve(old,'rollback')
            rollback=json.dumps({'key':'rollback','backup_id':self.backup.pk,'target_id':old.pk})
            self.assertEqual(self.client.post(url,rollback,content_type='application/json').json()['state'],'healthy')

    def test_factory_configuration_and_simulated_endpoint_unknown(self):
        from .adapters import RailwayCLI
        with override_settings(RELEASE_TRANSPORT_FACTORY=None,RELEASE_RAILWAY={'project':'p','service':'s',
                'artifact_provider':lambda _:None,'validation_provider':lambda *_:None,'probe_provider':lambda _:None}):
            self.assertIsInstance(transport_factory(),RailwayCLI)
        f=self.fixture(); r=self.candidate(f)
        merge_release(r.pk,actor=self.owner,key='merged',adapter=LocalGit(f.path)); self.approve(r)
        self.client.force_login(self.owner)
        with override_settings(RELEASE_TRANSPORT_FACTORY=lambda:SimulatedTransport(ambiguous=True)):
            response=self.client.post(f'/releases/{r.pk}/deployments/',json.dumps({'key':'ambiguous','backup_id':self.backup.pk}),content_type='application/json')
            self.assertEqual(response.json()['state'],'unknown')


class MergeRaceTests(TransactionTestCase):
    def test_overlapping_requests_only_one_git_mutation(self):
        setup=ActivationTests(); setup.setUp()
        self.addCleanup(setup.doCleanups)
        f=GitFixture()
        try:
            r=setup.candidate(f)
            entered,finish=Event(),Event()
            class BlockingGit(LocalGit):
                def merge(self,release):
                    entered.set()
                    if not finish.wait(5): raise TimeoutError()
                    return super().merge(release)
            def worker():
                close_old_connections()
                try: return merge_release(r.pk,actor=setup.owner,key='first',adapter=BlockingGit(f.path)).state
                finally: close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(worker)
                try:
                    self.assertTrue(entered.wait(5))
                    with self.assertRaises((ValidationError,OperationalError)):
                        merge_release(r.pk,actor=setup.owner,key='second',adapter=LocalGit(f.path))
                finally: finish.set()
                self.assertEqual(future.result(),'merged')
            self.assertEqual(MergeAttempt.objects.count(),1)
        finally: f.temp.cleanup()
