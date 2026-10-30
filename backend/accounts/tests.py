from django.test import TestCase
from django.db import IntegrityError, transaction
from .models import User


class SupervisorAdminTests(TestCase):
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
