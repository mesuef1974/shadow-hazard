"""حدّ محاولات الدخول الفاشلة (ADR 0010): المنصة على الإنترنت العام بثلاثة أسماء معروفة.

قفلان: زوج (اسم، عنوان) بعد PAIR_LIMIT محاولات، والاسم وحده بعد USER_LIMIT من عناوين شتى.
المفاتيح تُخزَّن مجزَّأة بالمفتاح السري، فلا يُحفظ عنوان IP ولا اسم في جدول القفل.
"""
import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import LoginThrottle

PAIR_LIMIT = 5
USER_LIMIT = 20
WINDOW = timedelta(minutes=15)
LOCK = timedelta(minutes=15)
KEEP = timedelta(days=1)
log = logging.getLogger("accounts.security")


def client_ip(request):
    """خلف الوسيط يحمل X-Real-IP عنوان العميل؛ بلا وسيط لا تُصدَّق ترويسة يكتبها العميل."""
    if request is None:
        return ""
    return request.META.get("HTTP_X_REAL_IP" if settings.BEHIND_PROXY else "REMOTE_ADDR", "")


def _name(username):
    return (username or "").strip().lower()


def _keys(username, ip):
    def digest(scope, value):
        return hashlib.sha256(f"{settings.SECRET_KEY}|{scope}|{value}".encode()).hexdigest()

    name = _name(username)
    return (digest("pair", f"{name}|{ip}"), PAIR_LIMIT), (digest("user", name), USER_LIMIT)


def is_locked(username, ip):
    keys = [key for key, _ in _keys(username, ip)]
    return LoginThrottle.objects.filter(key__in=keys, locked_until__gt=timezone.now()).exists()


def record_failure(username, ip):
    # الاسم غير الموجود لا يُخمَّن له سر، وتتبّعه يملأ الجدول بأسماء عشوائية.
    if not get_user_model()._default_manager.filter(username__iexact=_name(username)).exists():
        return
    now = timezone.now()
    with transaction.atomic():
        LoginThrottle.objects.filter(window_started__lt=now - KEEP).delete()
        for key, limit in _keys(username, ip):
            row, _ = LoginThrottle.objects.select_for_update().get_or_create(
                key=key, defaults={"window_started": now})
            if now - row.window_started > WINDOW:
                row.failures, row.window_started = 0, now
            row.failures += 1
            if row.failures >= limit:
                row.failures, row.window_started, row.locked_until = 0, now, now + LOCK
                log.warning("login locked after %s failed attempts", limit)
            row.save()


def reset(username, ip):
    """دخول ناجح يصفّر عدّاد الزوج وحده؛ عدّاد الاسم يخصّ عناوين أخرى ويزول بانقضاء نافذته."""
    LoginThrottle.objects.filter(key=_keys(username, ip)[0][0]).delete()
