from django.contrib import admin
from . import models
class ReadOnlyRegistryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None): return request.user.is_active and request.user.is_superuser
for model in [models.Service,models.ServiceVersion,models.Installation,models.Actor,models.ExecutionSession,models.RegistryTask,models.Artifact,models.UsageEvent,models.BrandDirection,models.BrandColor,models.BrandTypography,models.BrandAsset,models.FailureRecord,models.RegistryEvent]:
    admin.site.register(model,ReadOnlyRegistryAdmin)
