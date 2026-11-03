from django.contrib import admin
from .models import TrackedSession, TokenSample, SyncRun, CollectorState
admin.site.register(TrackedSession)

class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False

admin.site.register(TokenSample,ReadOnly)

@admin.register(SyncRun)
class SyncRunAdmin(ReadOnly):
    list_display=('started_at','finished_at','success','added','error_code','message')
    list_filter=('success','error_code')
    ordering=('-started_at',)

admin.site.register(CollectorState,ReadOnly)
