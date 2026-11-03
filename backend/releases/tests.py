from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from workspace.models import Project, Phase, Task
from operations.models import BackupRun
from .models import Approval, Deployment, EnvironmentLock
from .services import create_release, act, deploy, merge_release
from .adapters import SimulatedTransport, RailwayCLI


class ReleaseTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create(username='mesuef', role='supervisor')
        self.writer = User.objects.create(username='saud', role='student')
        self.reviewer = User.objects.create(username='mubarak', role='student')
        project = Project.objects.create(name='test')
        from django.test import override_settings
        project_setting = override_settings(RELEASE_PROJECT_ID=project.pk)
        project_setting.enable()
        self.addCleanup(project_setting.disable)
        phase = Phase.objects.create(project=project, title='test', order=1)
        self.task = Task.objects.create(phase=phase, title='test', assignee=self.writer, creator=self.owner)
        self.backup = BackupRun.objects.create(kind='pre_migration', state='success', sha256='a'*64, size_bytes=10, finished_at=timezone.now())
        self.n = 0

    def action(self, release, action, actor=None, **data):
        self.n += 1
        return act(release.pk, actor=actor or self.owner, action=action, key=f'event-{self.n}', data=data)

    def candidate(self, merged=True):
        r = create_release(actor=self.writer, task_id=self.task.pk, commit='a'*40, environment='staging',
            repository='test', source_ref='refs/heads/feature', target_branch='refs/heads/main', target_commit='c'*40,
            manifest=dict.fromkeys(['artifact_sha256','migration_plan_sha256','settings_sha256'], 'b'*64))
        self.action(r, 'pin', self.writer)
        if merged:
            for kind in ['tests','migration','settings']:
                self.action(r, 'evidence', kind=kind, reference='test:passed', accepted=True)
            self.action(r, 'review', self.reviewer, accepted=True)
            from types import SimpleNamespace
            self.n += 1
            merge_release(r.pk, actor=self.owner, key=f'event-{self.n}', adapter=SimpleNamespace(merge=lambda r:r.commit))
        return r

    def approve(self, r, purpose='deploy'):
        self.action(r, 'approve', fingerprint=r.fingerprint, environment=r.environment, purpose=purpose)

    def run_deploy(self, r, key='deployment', adapter=None, target=None):
        return deploy(r.pk, actor=self.owner, key=key, backup_id=self.backup.pk,
            adapter=adapter or SimulatedTransport(), target_id=target.pk if target else None)

    def test_independent_review_and_evidence_gate(self):
        r = self.candidate(False)
        with self.assertRaises(PermissionDenied): self.action(r, 'review', self.writer, accepted=True)
        with self.assertRaises(ValidationError): self.action(r, 'merge')

    def test_pin_prevents_version_change(self):
        r = self.candidate(False)
        with self.assertRaises(PermissionDenied): self.action(r, 'revise', self.writer, commit='c'*40)
        r.refresh_from_db(); r.commit='c'*40
        with self.assertRaises(ValidationError): r.save()

    def test_deployment_api_disabled_and_csrf_protected(self):
        from django.test import Client
        r = self.candidate()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(f'/releases/{r.pk}/deployments/', '{}', content_type='application/json').status_code, 503)
        client = Client(enforce_csrf_checks=True); client.force_login(self.owner)
        self.assertEqual(client.post(f'/releases/{r.pk}/actions/', '{}', content_type='application/json').status_code, 403)

    def test_approval_permissions_fingerprint_and_withdrawal(self):
        r = self.candidate()
        with self.assertRaises(PermissionDenied): self.action(r, 'approve', self.writer)
        with self.assertRaises(ValidationError): self.action(r, 'approve', fingerprint='wrong', environment='staging')
        self.approve(r)
        self.action(r, 'withdraw')
        with self.assertRaises(PermissionDenied): self.run_deploy(r)

    def test_duplicate_and_conflicting_event(self):
        r = self.candidate()
        event = act(r.pk, actor=self.owner, action='withdraw', key='same')
        self.assertEqual(act(r.pk, actor=self.owner, action='withdraw', key='same').pk, event.pk)
        with self.assertRaises(ValidationError): act(r.pk, actor=self.owner, action='approve', key='same')

    def test_success_and_deployment_idempotency(self):
        r = self.candidate(); self.approve(r)
        adapter = SimulatedTransport()
        first = self.run_deploy(r, adapter=adapter)
        self.assertEqual(first.state, 'healthy')
        self.assertEqual(self.run_deploy(r, adapter=adapter).pk, first.pk)
        self.assertEqual(adapter.calls, 1)
        with self.assertRaises(PermissionDenied): self.run_deploy(r, key='new')

    def test_migration_preflight_failure_does_not_publish(self):
        r = self.candidate(); self.approve(r)
        adapter = SimulatedTransport(preflight_ok=False)
        self.assertEqual(self.run_deploy(r, adapter=adapter).state, 'failed')
        self.assertEqual(adapter.calls, 0)

    def test_health_failure(self):
        r = self.candidate(); self.approve(r)
        self.assertEqual(self.run_deploy(r, adapter=SimulatedTransport(healthy=False)).state, 'failed')

    def test_ambiguous_outcome_blocks_other_release_and_retry(self):
        r = self.candidate(); self.approve(r)
        self.assertEqual(self.run_deploy(r, adapter=SimulatedTransport(ambiguous=True)).state, 'unknown')
        with self.assertRaises(ValidationError): self.run_deploy(r, key='retry')
        other = self.candidate(); self.approve(other)
        with self.assertRaises(ValidationError): self.run_deploy(other, key='parallel')
        self.assertEqual(Deployment.objects.count(), 1)

    def test_backup_required(self):
        r = self.candidate(); self.approve(r)
        self.backup.state='failed'; self.backup.save()
        with self.assertRaises(ValidationError): self.run_deploy(r)

    def test_rollback_requires_known_target_and_explicit_approvals(self):
        old = self.candidate(); self.approve(old); self.run_deploy(old, key='old')
        current = self.candidate(); self.approve(current, 'rollback')
        with self.assertRaises(PermissionDenied): self.run_deploy(current, key='rollback', target=old)
        self.approve(old, 'rollback')
        self.assertEqual(self.run_deploy(current, key='rollback', target=old).state, 'healthy')

    def test_cli_rejects_artifact_mismatch_without_execution(self):
        r = self.candidate()
        calls=[]
        adapter=RailwayCLI(project='p',service='s',artifact_provider=lambda _: {'commit':'bad','sha256':'bad'},
            preflight=lambda *_: {}, probes=lambda _: {}, runner=lambda *a,**k:calls.append(a))
        with self.assertRaises(ValueError): adapter.publish(r,'x')
        self.assertEqual(calls, [])


class ConcurrencyTests(TransactionTestCase):
    def test_two_requests_cannot_publish_same_environment(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import close_old_connections, OperationalError
        setup = ReleaseTests()
        setup.setUp()
        self.addCleanup(setup.doCleanups)
        first = setup.candidate(); setup.approve(first)
        second = setup.candidate(); setup.approve(second)
        entered, finish = Event(), Event()

        class BlockingTransport(SimulatedTransport):
            def publish(self, release, key):
                entered.set()
                if not finish.wait(5): raise TimeoutError()
                return super().publish(release, key)

        def worker():
            close_old_connections()
            try:
                return setup.run_deploy(first, key='first', adapter=BlockingTransport()).state
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises((ValidationError, OperationalError)):
                    setup.run_deploy(second, key='second')
            finally:
                finish.set()
            self.assertEqual(future.result(), 'healthy')
        self.assertEqual(Deployment.objects.count(), 1)
