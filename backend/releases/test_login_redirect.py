"""Anonymous release API calls go to the real login page, not a 404 route."""
from django.test import TestCase


class ReleaseApiLoginRedirectTests(TestCase):
    def test_anonymous_requests_redirect_to_platform_login(self):
        for path in ['/releases/', '/releases/1/']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertEqual(response['Location'], f'/login/?next={path}', path)
