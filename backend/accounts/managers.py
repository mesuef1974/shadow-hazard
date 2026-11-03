from django.contrib.auth.models import UserManager


class ProjectUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", "supervisor")
        return super().create_superuser(username, email, password, **extra_fields)
