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
