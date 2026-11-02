from django.conf import settings
from django.db import models


class SimulationRun(models.Model):
    project = models.ForeignKey('workspace.Project', on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    scenario_key = models.CharField(max_length=80)
    title = models.CharField(max_length=160)
    version = models.CharField(max_length=80)
    config = models.JSONField()
    metrics = models.JSONField()
    result = models.JSONField()
    digest = models.CharField(max_length=64)

    class Meta:
        ordering = ['-pk']
