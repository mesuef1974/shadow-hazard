from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from . import throttle
from .models import LoginThrottle, User

GOOD = "test-only-strong-pass-617!"


class LoginThrottleTests(TestCase):
    def setUp(self):
        User.objects.create_superuser(username="mesuef", email="", password=GOOD)

    def attempt(self, password, ip="203.0.113.5", path="/login/"):
        return self.client.post(path, {"username": "mesuef", "password": password}, REMOTE_ADDR=ip)

    def fail_login(self, times, ip="203.0.113.5"):
        for _ in range(times):
            self.assertEqual(self.attempt("wrong", ip).status_code, 200)

    def test_pair_locks_after_limit_even_with_right_password(self):
        self.fail_login(throttle.PAIR_LIMIT)
        response = self.attempt(GOOD)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "محاولات دخول كثيرة")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_other_address_is_not_locked_by_one_attacker(self):
        self.fail_login(throttle.PAIR_LIMIT)
        self.assertEqual(self.attempt(GOOD, ip="198.51.100.9").status_code, 302)

    def test_admin_login_shares_the_lock(self):
        self.assertEqual(self.attempt(GOOD, path="/admin/login/").status_code, 302)
        self.client.post("/logout/")
        self.fail_login(throttle.PAIR_LIMIT)
        response = self.attempt(GOOD, path="/admin/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_success_resets_the_pair_counter(self):
        self.fail_login(throttle.PAIR_LIMIT - 1)
        self.assertEqual(self.attempt(GOOD).status_code, 302)
        self.client.post("/logout/")
        self.fail_login(throttle.PAIR_LIMIT - 1)
        self.assertEqual(self.attempt(GOOD).status_code, 302)

    def test_lock_expires(self):
        self.fail_login(throttle.PAIR_LIMIT)
        past = timezone.now() - timedelta(seconds=1)  # زمن-ثابت
        LoginThrottle.objects.update(locked_until=past)
        self.assertEqual(self.attempt(GOOD).status_code, 302)

    def test_username_locks_across_many_addresses(self):
        for n in range(throttle.USER_LIMIT):
            self.fail_login(1, ip=f"203.0.113.{n}")
        self.assertContains(self.attempt(GOOD, ip="198.51.100.9"), "محاولات دخول كثيرة")

    def test_unknown_username_leaves_no_rows(self):
        for _ in range(throttle.PAIR_LIMIT + 1):
            self.client.post("/login/", {"username": "nobody", "password": "x"})
        self.assertEqual(LoginThrottle.objects.count(), 0)

    def test_failures_outside_the_window_do_not_add_up(self):
        self.fail_login(throttle.PAIR_LIMIT - 1)
        old = timezone.now() - throttle.WINDOW * 2  # زمن-ثابت
        LoginThrottle.objects.update(window_started=old)
        self.fail_login(throttle.PAIR_LIMIT - 1)
        self.assertEqual(self.attempt(GOOD).status_code, 302)

    def test_success_keeps_the_username_counter(self):
        self.fail_login(1)
        self.assertEqual(self.attempt(GOOD).status_code, 302)
        self.assertEqual(LoginThrottle.objects.count(), 1)

    @override_settings(BEHIND_PROXY=True)
    def test_behind_proxy_the_real_ip_header_identifies_the_client(self):
        for _ in range(throttle.PAIR_LIMIT):
            self.client.post("/login/", {"username": "mesuef", "password": "wrong"},
                             REMOTE_ADDR="10.0.0.1", HTTP_X_REAL_IP="203.0.113.5")
        other = self.client.post("/login/", {"username": "mesuef", "password": GOOD},
                                 REMOTE_ADDR="10.0.0.1", HTTP_X_REAL_IP="198.51.100.9")
        self.assertEqual(other.status_code, 302)

    def test_direct_client_cannot_spoof_the_header(self):
        for n in range(throttle.PAIR_LIMIT):
            self.client.post("/login/", {"username": "mesuef", "password": "wrong"},
                             REMOTE_ADDR="203.0.113.5", HTTP_X_REAL_IP=f"198.51.100.{n}")
        self.assertContains(self.attempt(GOOD), "محاولات دخول كثيرة")


class HealthAndProxyTests(TestCase):
    def test_health_needs_no_login(self):
        response = self.client.get("/healthz/")
        self.assertEqual((response.status_code, response.content), (200, b"ok"))

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_health_is_exempt_from_https_redirect_but_pages_are_not(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
        self.assertEqual(self.client.get("/login/").status_code, 301)

    @override_settings(SECURE_SSL_REDIRECT=True,
                       SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_proxy_header_stops_the_redirect_loop(self):
        response = self.client.get("/login/", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)
