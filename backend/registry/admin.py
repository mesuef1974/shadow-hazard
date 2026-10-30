import hashlib
import re
from pathlib import Path
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.urls import path, reverse
from django.utils.html import format_html
from . import models

admin.site.site_header = 'إدارة مشروع ظل الخطر'
admin.site.site_title = 'إدارة ظل الخطر'
admin.site.index_title = 'سجل المشروع والهوية'

class ReadOnlyRegistryAdmin(admin.ModelAdmin):
    @admin.display(description='المنفّذ')
    def usage_actor(self, obj): return obj.session.actor
    list_per_page = 30
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
    empty_value_display = 'غير مسجل'
    class Media:
        css = {'all': ('registry/admin.css',)}
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None): return request.user.is_active and request.user.is_superuser

PREVIEW_TYPES = {'.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp', '.svg':'image/svg+xml'}
def asset_preview(artifact):
    if Path(artifact.path).suffix.lower() not in PREVIEW_TYPES:
        return 'لا تتوفر معاينة مرئية لهذا النوع؛ راجع المسار والبصمة.'
    return format_html('<img class="registry-preview" src="{}" alt="معاينة {}" loading="lazy">', reverse('admin:registry_artifact_preview', args=[artifact.pk]), artifact.path)

@admin.register(models.Artifact)
class ArtifactAdmin(ReadOnlyRegistryAdmin):
    list_display = ('path', 'preview', 'media_type', 'created_at')
    search_fields = ('path', 'sha256')
    list_filter = ('media_type',)
    readonly_fields = ('created_at', 'preview')
    @admin.display(description='المعاينة')
    def preview(self, obj): return asset_preview(obj)
    def get_urls(self):
        return [path('<uuid:object_id>/preview/', self.admin_site.admin_view(self.preview_file), name='registry_artifact_preview')] + super().get_urls()
    def preview_file(self, request, object_id):
        if not self.has_view_permission(request): raise PermissionDenied
        obj = self.get_object(request, str(object_id))
        if obj is None: raise Http404
        root = (settings.BASE_DIR.parent / 'docs' / 'brand').resolve()
        target = (settings.BASE_DIR.parent / obj.path).resolve()
        if not target.is_relative_to(root) or target.suffix.lower() not in PREVIEW_TYPES: raise Http404
        try:
            if target.stat().st_size > 10 * 1024 * 1024: raise Http404
            data = target.read_bytes()
        except OSError as exc: raise Http404 from exc
        if hashlib.sha256(data).hexdigest() != obj.sha256: raise Http404('الملف لا يطابق النسخة الموثقة')
        response = HttpResponse(data, content_type=PREVIEW_TYPES[target.suffix.lower()])
        # SVG is an image, never inline markup; sandbox blocks active content on direct navigation.
        response['Content-Security-Policy'] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response

@admin.register(models.BrandColor)
class ColorAdmin(ReadOnlyRegistryAdmin):
    list_display = ('name', 'direction', 'swatch', 'token', 'hex_value')
    search_fields = ('name', 'direction__name', 'token', 'hex_value')
    list_filter = ('direction',)
    list_select_related = ('direction',)
    readonly_fields = ('created_at', 'swatch')
    @admin.display(description='معاينة اللون')
    def swatch(self, obj):
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', obj.hex_value): return 'رمز غير صالح'
        return format_html('<span class="registry-swatch" style="background-color:{}" aria-hidden="true"></span> <bdi>{}</bdi>', obj.hex_value, obj.hex_value)

@admin.register(models.BrandAsset)
class BrandAssetAdmin(ReadOnlyRegistryAdmin):
    list_display = ('direction', 'preview', 'kind', 'artifact', 'usage_rule')
    search_fields = ('direction__name', 'artifact__path', 'kind', 'usage_rule')
    list_filter = ('direction', 'kind')
    list_select_related = ('direction', 'artifact')
    readonly_fields = ('created_at', 'preview')
    @admin.display(description='معاينة الأصل')
    def preview(self, obj): return asset_preview(obj.artifact)

# Explicit per-table presentation; all inherit the unchanged read-only permission policy.
TABLES = [
    (models.Service, ('name','kind','state','purpose','created_at'), ('name','key','purpose'), ('kind','state'), ()),
    (models.ServiceVersion, ('service','version_label','source','created_at'), ('service__name','version_label','source','sha256'), ('service__kind',), ('service',)),
    (models.Installation, ('version','explanation','explanation_reviewed','root_alias','relative_path','presence','observed_at'), ('version__service__name','explanation','source_description','relative_path','external_key'), ('root_alias','presence','explanation_reviewed'), ('version__service',)),
    (models.Actor, ('name','kind','user'), ('name','key','user__username'), ('kind',), ('user',)),
    (models.ExecutionSession, ('name','actor','client','model','charter_status','created_at'), ('name','external_key','actor__name','model'), ('client','charter_status'), ('actor',)),
    (models.RegistryTask, ('title','purpose','created_at'), ('title','key','purpose'), ('created_at',), ()),
    (models.UsageEvent, ('version','usage_actor','task','session','purpose','status','recorded_at'), ('event_key','version__service__name','task__title','purpose','method','result'), ('status','version__service__kind','recorded_at'), ('version__service','task','session__actor')),
    (models.BrandDirection, ('name','english_name','mascot_name','status','approved_by','approved_at'), ('name','english_name','mascot_name','story'), ('status',), ('approved_by',)),
    (models.BrandTypography, ('family','direction','script','font_version'), ('family','direction__name'), ('direction','script'), ('direction','font_version__service')),
    (models.FailureRecord, ('symptom','task','correction','created_at'), ('key','symptom','cause','correction','prevention','task__title'), ('session','created_at'), ('task','session')),
    (models.RegistryEvent, ('action','key','session','created_at'), ('key','action','session__name'), ('action','created_at'), ('session',)),
]
for model, columns, search, filters, related in TABLES:
    admin_class = type(model.__name__ + 'Admin', (ReadOnlyRegistryAdmin,), {
        'list_display': columns, 'search_fields': search, 'list_filter': filters,
        'list_select_related': related, '__module__': __name__,
    })
    admin.site.register(model, admin_class)

@admin.register(models.PythonPackage)
class PythonPackageAdmin(ReadOnlyRegistryAdmin):
    list_display = ('name', 'version', 'explanation', 'is_installed', 'observed_at')
    search_fields = ('name', 'version', 'summary')
    list_filter = ('is_installed', 'python_version')

@admin.register(models.PythonPackageEvent)
class PackageEventAdmin(ReadOnlyRegistryAdmin):
    list_display = ('package','action','old_version','new_version','actor','task','reason','created_at')
    search_fields = ('package__name','reason','actor__name','task__title')
    list_filter = ('action','created_at')
    list_select_related = ('package','actor','task')

@admin.register(models.SkillChange)
class SkillChangeAdmin(ReadOnlyRegistryAdmin):
    list_display = ('installation','action','old_version','new_version','created_at')
    search_fields = ('installation__version__service__name','installation__relative_path','reason')
    list_filter = ('action','installation__root_alias','created_at')
    list_select_related = ('installation__version__service','old_version__service','new_version__service')

@admin.register(models.SkillScan)
class SkillScanAdmin(ReadOnlyRegistryAdmin):
    list_display = ('root_alias','success','files_count','scanned_at','succeeded_at','error')
    search_fields = ('root_alias','error')
    list_filter = ('success',)
