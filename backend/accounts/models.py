from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from .managers import ProjectUserManager

class User(AbstractUser):
    objects = ProjectUserManager()
    role = models.CharField("دور المشروع", max_length=16, choices=[("supervisor", "مشرف"), ("student", "طالب")], default="student")
    class Meta:
        verbose_name = "مستخدم"
        verbose_name_plural = "المستخدمون"
        constraints = [models.CheckConstraint(condition=Q(username="mesuef", role="supervisor") | Q(username__in=["saud", "mubarak"], role="student", is_staff=False, is_superuser=False), name="only_three_named_accounts")]

    def clean(self):
        super().clean()
        # نموذج الإدارة لا يحوي is_staff فيتخطى Django القيد ويسقط عند الحفظ بخطأ 500.
        try:
            self._meta.constraints[0].validate(type(self), self)
        except ValidationError:
            raise ValidationError('الحسابات المسموحة: mesuef مشرفًا، وsaud وmubarak طالبين بلا صلاحية إدارة.')
