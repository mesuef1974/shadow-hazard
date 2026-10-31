from django.contrib import admin
from .models import AutomationIssue, BackupPolicy, BackupRun, ReviewAssignment


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReviewAssignment)
class ReviewAssignmentAdmin(ReadOnlyAdmin):
    list_display=['source_job','review_job','review_state','review_result','created_at']
    list_filter=['review_job__state']
    search_fields=['source_job__id','review_job__id']
    readonly_fields=[field.name for field in ReviewAssignment._meta.fields]+['review_result']
    list_select_related=['source_job','review_job']

    @admin.display(description='حالة المراجعة')
    def review_state(self,obj):return obj.state

    @admin.display(description='نتيجة مراجعة الأصل')
    def review_result(self,obj):
        receipt=obj.result_document
        return f'نتيجة موثقة — إيصال {receipt.pk}: {receipt.summary}' if receipt else 'بانتظار نتيجة منفصلة موثقة'


@admin.register(AutomationIssue)
class AutomationIssueAdmin(ReadOnlyAdmin):
    list_display = ("id", "severity", "code", "title", "job", "state", "first_seen", "last_seen")
    list_filter = ("state", "severity", "code")
    search_fields = ("title", "code", "fingerprint")
    readonly_fields = tuple(field.name for field in AutomationIssue._meta.fields)


@admin.register(BackupPolicy)
class BackupPolicyAdmin(ReadOnlyAdmin):
    list_display = ("key", "enabled", "interval_hours", "retained_successful", "before_migration", "updated_at")
    readonly_fields = tuple(field.name for field in BackupPolicy._meta.fields)


@admin.register(BackupRun)
class BackupRunAdmin(ReadOnlyAdmin):
    list_display = ("id", "kind", "state", "relative_path", "integrity_result", "started_at", "finished_at")
    list_filter = ("kind", "state", "integrity_result")
    readonly_fields = tuple(field.name for field in BackupRun._meta.fields)
