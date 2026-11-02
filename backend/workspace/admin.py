from django.contrib import admin
from .models import Project,Phase,Task,TaskUpdate,Decision,DocumentAccess
from .models import ModelPolicy, SessionCharter
admin.site.register(ModelPolicy)
admin.site.register(SessionCharter)
for model in [Project,Phase,DocumentAccess]:admin.site.register(model)

class HistoryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

for model in [Task,TaskUpdate,Decision]:admin.site.register(model,HistoryAdmin)

from .models import ProjectMemory,ProjectDocumentation
admin.site.register(ProjectMemory)
admin.site.register(ProjectDocumentation,HistoryAdmin)

from .models import AutoRenewalPolicy,AutoRenewalLatch
@admin.register(AutoRenewalPolicy)
class AutoRenewalPolicyAdmin(HistoryAdmin):
    list_display = ('charter', 'enabled', 'owner', 'activation_receipt', 'updated_at')

@admin.register(AutoRenewalLatch)
class AutoRenewalLatchAdmin(HistoryAdmin):
    list_display = ('charter', 'source_thread_id', 'state', 'handover', 'renewal', 'updated_at')
    list_filter = ('state',)
