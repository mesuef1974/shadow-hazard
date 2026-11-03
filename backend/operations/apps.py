from django.apps import AppConfig


class OperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "operations"
    verbose_name = "التشغيل والنسخ الاحتياطي"

    def ready(self):
        from . import signals  # noqa: F401
