from django.contrib import admin

from .models import AiModel, ClaudeMessage, ClaudeSession, GitBranch, ImportRun, SeatQuotaSnapshot, UsageSource


class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ClaudeMessage)
class ClaudeMessageAdmin(ReadOnly):
    list_display = ('at', 'model', 'session', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'is_subagent')
    list_filter = ('model', 'is_subagent')
    date_hierarchy = 'at'


@admin.register(ClaudeSession)
class ClaudeSessionAdmin(ReadOnly):
    list_display = ('session_id', 'surface', 'project', 'cwd')
    list_filter = ('surface',)


@admin.register(ImportRun)
class ImportRunAdmin(ReadOnly):
    list_display = ('started_at', 'ok', 'files_read', 'messages_new', 'messages_updated', 'skipped_lines', 'error')


admin.site.register(AiModel)
admin.site.register(GitBranch, ReadOnly)
admin.site.register(UsageSource, ReadOnly)
admin.site.register(SeatQuotaSnapshot)
