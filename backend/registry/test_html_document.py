from django.contrib.auth import get_user_model
from django.test import TestCase

PAGE = '/documents/exercises/bus-shadow-explorer.html'


class HtmlDocumentTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create(username='mesuef', role='supervisor'))

    def test_html_opens_in_the_page_inside_a_sandbox(self):
        response = self.client.get(PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')
        self.assertEqual(response['Content-Security-Policy'], 'sandbox allow-scripts')
        self.assertNotIn('attachment', response.get('Content-Disposition', ''))
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_download_stays_an_attachment(self):
        response = self.client.get(PAGE + '?download=1')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_team_member_without_access_gets_nothing(self):
        self.client.force_login(get_user_model().objects.create(username='saud', role='student'))
        self.assertEqual(self.client.get(PAGE).status_code, 404)
