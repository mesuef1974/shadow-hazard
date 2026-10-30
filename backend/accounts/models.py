from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
class User(AbstractUser):
    role = models.CharField(max_length=16, choices=[("supervisor", "مشرف"), ("student", "طالب")], default="student")
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(username="sufyan", role="supervisor") | Q(username__in=["saud", "mubarak"], role="student", is_staff=False, is_superuser=False), name="only_three_named_accounts")]
