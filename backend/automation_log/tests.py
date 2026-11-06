import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .models import Card, Event
from .services import ingest_local, ingest_prs, ingest_runs, snapshot, sync_due

PR = {'number': 7, 'title': 'عمل', 'headRefName': 'chore/a', 'baseRefName': 'main', 'state': 'MERGED',
      'createdAt': '2026-11-05T10:00:00Z', 'mergedAt': '2026-11-05T11:00:00Z',
      'closedAt': '2026-11-05T11:00:00Z', 'url': 'https://github.com/x/y/pull/7',
      'mergeCommit': {'oid': 'abc1234' + '0' * 33}}
RUN = {'databaseId': 1, 'headBranch': 'chore/a', 'headSha': 'f' * 40, 'event': 'push',
       'status': 'completed', 'conclusion': 'failure', 'createdAt': '2026-11-05T09:00:00Z',
       'updatedAt': '2026-11-05T09:30:00Z', 'url': 'https://github.com/x/y/actions/runs/1'}
DEPLOY = json.dumps({'at': '2026-11-05T12:00:00+00:00', 'kind': 'deploy', 'result': 'pass',
                     'detail': 'نُشر abc1234'}, ensure_ascii=False)
URL = '/sessions/automation/flow/'


class IngestTests(TestCase):
    def test_ingest_is_idempotent(self):
        self.assertEqual(ingest_prs([PR]), 2)
        self.assertEqual(ingest_prs([PR]), 0)
        self.assertEqual(ingest_runs([RUN]), 1)
        self.assertEqual(ingest_runs([RUN]), 0)
        self.assertEqual(ingest_local([DEPLOY, 'not json']), 1)
        self.assertEqual(ingest_local([DEPLOY]), 0)
        self.assertEqual(Card.objects.count(), 1)

    def test_deploy_links_to_merged_card(self):
        ingest_prs([PR])
        ingest_local([DEPLOY])
        self.assertEqual(Event.objects.get(stage='deploy').card.branch, 'chore/a')

    def test_running_ci_is_not_recorded_until_complete(self):
        self.assertEqual(ingest_runs([{**RUN, 'status': 'in_progress'}]), 0)

    def test_history_cannot_be_changed_or_deleted(self):
        ingest_runs([RUN])
        event = Event.objects.get()
        event.detail = 'تزوير'
        for action in (event.save, event.delete, Event.objects.all().delete,
                       lambda: Event.objects.update(detail='x')):
            with self.assertRaises(TypeError):
                action()

    def test_worker_step_is_throttled(self):
        with patch('automation_log.services.sync', return_value=3) as sync, \
                patch.dict('automation_log.services._last', {'at': 0.0}):
            self.assertEqual(sync_due(), 3)
            self.assertEqual(sync_due(), 0)
        self.assertEqual(sync.call_count, 1)


class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.boss = users.objects.create_user(username='mesuef', role='supervisor')
        cls.other = users.objects.create_user(username='saud', role='student')
        ingest_prs([PR])
        ingest_runs([RUN])
        ingest_local([DEPLOY])

    def test_only_supervisor(self):
        for path in ('', 'data.json', 'export.csv'):
            self.assertEqual(self.client.get(URL + path).status_code, 302)
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(URL + path).status_code, 403)
            self.client.logout()

    def test_snapshot_reaches_deployed_and_filters(self):
        self.client.force_login(self.boss)
        data = self.client.get(URL + 'data.json').json()
        self.assertEqual(data['counts']['deployed'], 1)
        self.assertEqual(data['total'], 4)
        data = self.client.get(URL + 'data.json', {'result': 'fail'}).json()
        self.assertEqual([e['stage'] for e in data['events']], ['ci'])
        data = self.client.get(URL + 'data.json', {'from': 'bad', 'q': 'نُشر'}).json()
        self.assertEqual(data['total'], 1)

    def test_search_narrows_the_cards_too(self):
        wanted = Card.objects.create(branch='fix/wanted', title='مطلوب')
        other = Card.objects.create(branch='fix/other', title='غيره')
        for index, card in enumerate((wanted, other)):
            Event.objects.create(external_id=f'search-{index}', stage='ci', source='github', card=card,
                                 occurred_at='2026-11-05T12:00:00Z', result='pass', detail=card.branch)
        found = [card['id'] for card in snapshot({'q': 'wanted'})['cards']]
        self.assertEqual(found, [wanted.pk])
        kept = [card['id'] for card in snapshot({'card': str(wanted.pk)})['cards']]
        self.assertIn(other.pk, kept)

    def test_csv_neutralizes_formulas(self):
        Event.objects.create(external_id='x', stage='guard', result='info', source='local',
                             occurred_at='2026-11-05T12:00:00Z', detail='=HYPERLINK("x")')
        self.client.force_login(self.boss)
        body = self.client.get(URL + 'export.csv').content.decode('utf-8')
        self.assertIn("'=HYPERLINK", body)

    def test_page_renders(self):
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(URL), 'مسار البطاقة')

    def test_events_table_names_its_columns(self):
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(URL), '<th scope="col"', count=5)

    @override_settings(HIDE_AGENT_SECTION=True)
    def test_page_survives_the_retired_section_redirect(self):
        # مشغّل الاختبار يعطّل هذا التحويل، فمرّت الصفحة في الاختبار وحُجبت على المنصة.
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get(URL).status_code, 200)
