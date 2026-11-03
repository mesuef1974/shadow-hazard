import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.conf import settings
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.db import IntegrityError, transaction, close_old_connections
from django.core.exceptions import PermissionDenied
from workspace.models import Project, Phase, Task, SessionCharter, ModelPolicy, ModelJob
from .models import Release
from .services import create_release_once
from .tests import ReleaseTests


class CreationTests(TestCase):
    setUp = ReleaseTests.setUp

    def payload(self):
        return {'task_id':self.task.pk,'commit':'a'*40,'environment':'staging','repository':'test',
            'source_ref':'refs/heads/feature','target_branch':'refs/heads/main','target_commit':'c'*40,
            'manifest':dict.fromkeys(['artifact_sha256','migration_plan_sha256','settings_sha256'],'b'*64)}

    def post(self, data=None, key='creation-key', client=None):
        client=client or self.client
        return client.post('/releases/',json.dumps(self.payload() if data is None else data),
            content_type='application/json',HTTP_IDEMPOTENCY_KEY=key)

    def job(self, project):
        policy,_=ModelPolicy.objects.get_or_create(category='release-tests')
        charter=SessionCharter.objects.create(key=f'backend-{project.pk}',project=project,policy=policy)
        return ModelJob.objects.create(charter=charter,owner=self.owner,prompt='test',requested_model='test',requested_thinking='low')

    def test_replay_across_clients_and_payload_conflict(self):
        self.client.force_login(self.writer)
        first=self.post(); self.assertEqual(first.status_code,201)
        another=Client(); another.force_login(self.writer)
        replay=self.post(client=another)
        self.assertEqual(replay.status_code,200); self.assertEqual(first.json(),replay.json())
        changed={**self.payload(),'commit':'d'*40}
        self.assertEqual(self.post(changed).status_code,409)
        self.assertEqual(Release.objects.count(),1)
        # A draft change does not redefine the original creation request.
        Release.objects.filter(pk=first.json()['id']).update(state='pinned')
        self.assertEqual(self.post().status_code,200)

    def test_key_required_and_actor_cannot_reuse_another_actors_key(self):
        self.client.force_login(self.writer)
        self.assertEqual(self.post(key='').status_code,400)
        self.assertEqual(self.post().status_code,201)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.post().status_code,409)
        self.writer.is_active=False
        with self.assertRaises(PermissionDenied): create_release_once(actor=self.writer,key='inactive',data=self.payload())

    def test_xor_and_project_validation_for_tasks_and_jobs(self):
        self.client.force_login(self.owner)
        project=self.task.phase.project
        job=self.job(project)
        other=Project.objects.create(name='other')
        phase=Phase.objects.create(project=other,title='other',order=1)
        foreign=Task.objects.create(phase=phase,title='other',assignee=self.writer,creator=self.owner)
        foreign_job=self.job(other)
        base=self.payload()
        invalid=[{**base,'task_id':None},{**base,'job_id':job.pk},
            {**base,'task_id':foreign.pk},{**base,'task_id':None,'job_id':foreign_job.pk},
            {**base,'task_id':999999},{**base,'task_id':True}]
        for i,data in enumerate(invalid):
            self.assertEqual(self.post(data,key=f'invalid-{i}').status_code,400)
        self.assertEqual(self.post({**base,'task_id':None,'job_id':job.pk},key='job').status_code,201)
        self.assertEqual(self.post(base,key='task').status_code,201)

    def test_database_enforces_xor_and_unique_key(self):
        row,_=create_release_once(actor=self.writer,key='db',data=self.payload())
        job=self.job(self.task.phase.project)
        for changes in [{'task_id':None},{'job_id':job.pk}]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Release.objects.filter(pk=row.pk).update(**changes)
        other,_=create_release_once(actor=self.writer,key='other',data=self.payload())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Release.objects.filter(pk=other.pk).update(creation_key='db')

    def test_default_project_comes_from_backend_charter(self):
        policy=ModelPolicy.objects.create(category='project-scope')
        SessionCharter.objects.create(key='backend',project=self.task.phase.project,policy=policy)
        with override_settings(RELEASE_PROJECT_ID=None):
            self.assertTrue(create_release_once(actor=self.writer,key='charter',data=self.payload())[1])

    def test_csrf_all_post_routes_and_valid_creation_token(self):
        from django.middleware.csrf import _get_new_csrf_string
        client=Client(enforce_csrf_checks=True); client.force_login(self.owner)
        for path in ['/releases/','/releases/1/actions/','/releases/1/merge/','/releases/1/deployments/']:
            self.assertEqual(client.post(path,'{}',content_type='application/json').status_code,403)
        token=_get_new_csrf_string(); client.cookies[settings.CSRF_COOKIE_NAME]=token
        response=client.post('/releases/',json.dumps(self.payload()),content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='csrf-valid',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,201)
        anonymous=Client()
        self.assertEqual(anonymous.post('/releases/','{}',content_type='application/json').status_code,302)


class CreationRaceTests(TransactionTestCase):
    def test_two_clients_same_key_create_at_most_one_and_replay(self):
        self.run_race(False)

    def test_two_clients_conflicting_payload_one_winner(self):
        self.run_race(True)

    def run_race(self, conflicting):
        setup=CreationTests(); setup.setUp(); self.addCleanup(setup.doCleanups)
        barrier=Barrier(2)
        clients=[Client(),Client()]
        for client in clients: client.force_login(setup.writer)
        def worker(client):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                data=setup.payload()
                if conflicting and client is clients[1]: data['commit']='d'*40
                return setup.post(data,client=client).status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(worker,clients))
        self.assertEqual(statuses.count(201),1)
        self.assertTrue(all(status in {200,201,409} for status in statuses))
        if conflicting: self.assertIn(409,statuses)
        self.assertEqual(Release.objects.count(),1)
        setup.client.force_login(setup.writer)
        winner=Release.objects.get()
        replay={**setup.payload(),'commit':winner.commit}
        self.assertEqual(setup.post(replay).status_code,200)
