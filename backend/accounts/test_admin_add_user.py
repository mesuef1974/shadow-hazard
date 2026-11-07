from django.contrib.auth import get_user_model
from django.test import TestCase

ADD = '/admin/accounts/user/add/'
PASSWORD = 'Kq7-unused-in-any-real-account'


class AdminAddUserTests(TestCase):
    def setUp(self):
        self.client.raise_request_exception = False
        self.client.force_login(get_user_model().objects.create_superuser('mesuef', password=PASSWORD))

    def add(self, username, role='student'):
        return self.client.post(ADD, {'username': username, 'role': role, 'usable_password': 'true',
                                      'password1': PASSWORD, 'password2': PASSWORD})

    def test_name_outside_the_three_accounts_shows_a_message(self):
        response = self.add('khalid')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'saud')
        self.assertFalse(get_user_model().objects.filter(username='khalid').exists())

    def test_named_student_with_supervisor_role_shows_a_message(self):
        self.assertEqual(self.add('saud', role='supervisor').status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='saud').exists())

    def test_named_student_is_created(self):
        self.assertEqual(self.add('mubarak').status_code, 302)
        self.assertTrue(get_user_model().objects.filter(username='mubarak', role='student').exists())
