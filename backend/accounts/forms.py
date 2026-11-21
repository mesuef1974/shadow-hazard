from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from . import throttle


class ThrottledAuthenticationForm(AuthenticationForm):
    """يقول للمقفول إنه مقفول، بدل رسالة «كلمة المرور خاطئة» التي تدفعه إلى محاولات أكثر."""

    def clean(self):
        if throttle.is_locked(self.cleaned_data.get("username"), throttle.client_ip(self.request)):
            raise ValidationError("محاولات دخول كثيرة. حاول مرة أخرى بعد 15 دقيقة.", code="locked")
        return super().clean()
