from django.test import TestCase
from django.conf import settings
from django.db import IntegrityError, transaction
from .models import User


class SupervisorAdminTests(TestCase):
    def test_platform_session_survives_foreign_local_app_cookie(self):
        User.objects.create_superuser(username="mesuef", email="", password="test-only-strong-pass-617!")
        response = self.client.post("/login/", {"username": "mesuef", "password": "test-only-strong-pass-617!"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(settings.SESSION_COOKIE_NAME, "shadow_platform_session")
        self.assertNotEqual(settings.CSRF_COOKIE_NAME, "csrftoken")
        session_key = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        # Another localhost application overwrites Django's default cookie.
        self.client.cookies["sessionid"] = "foreign-local-application-session"
        for path in ("/admin/", "/admin/registry/service/", "/admin/accounts/user/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.wsgi_request.user.is_authenticated)
                self.assertEqual(self.client.cookies[settings.SESSION_COOKIE_NAME].value, session_key)
        self.client.post("/logout/")
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_supervisor_can_login_and_view_registry(self):
        user = User.objects.create_user(username="mesuef", password="test-only-strong-pass-617!", role="supervisor", is_staff=True, is_superuser=True)
        self.assertTrue(self.client.login(username=user.username, password="test-only-strong-pass-617!"))
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/admin/registry/service/").status_code, 200)

    def test_previous_username_is_not_a_fourth_account(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(username="sufyan", role="supervisor", is_staff=True)

    def test_unset_password_cannot_authenticate(self):
        user = User(username="mesuef", role="supervisor", is_staff=True, is_superuser=True)
        user.set_unusable_password()
        user.save()
        self.assertFalse(user.has_usable_password())
        self.assertFalse(self.client.login(username="mesuef", password=""))

    def test_standard_superuser_command_assigns_supervisor_role(self):
        user = User.objects.create_superuser(username="mesuef", email="", password="test-only-strong-pass-617!")
        self.assertEqual(user.role, "supervisor")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
