from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from . import throttle


class ThrottledModelBackend(ModelBackend):
    """الفحص في الخلفية لا في النموذج، ليشمل صفحة الدخول ودخول الإدارة معًا."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None
        ip = throttle.client_ip(request)
        if throttle.is_locked(username, ip):
            raise PermissionDenied  # يوقف التحقق قبل مقارنة كلمة المرور.
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            throttle.record_failure(username, ip)
        else:
            throttle.reset(username, ip)
        return user
