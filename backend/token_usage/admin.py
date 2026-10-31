from django.contrib import admin
from .models import TrackedSession, TokenSample, SyncRun
admin.site.register(TrackedSession)

class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False

admin.site.register(TokenSample,ReadOnly)
admin.site.register(SyncRun,ReadOnly)
