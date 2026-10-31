from django.contrib import admin
from .models import TaskContext, EscalationRequest


@admin.register(TaskContext)
class TaskContextAdmin(admin.ModelAdmin):
    list_display=['job','kind','created_at']
    list_filter=['kind']
    search_fields=['job__prompt','charter']
    readonly_fields=[field.name for field in TaskContext._meta.fields]
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False


@admin.register(EscalationRequest)
class EscalationRequestAdmin(admin.ModelAdmin):
    list_display=['source_job','target_model','state','decided_by','decided_at']
    list_filter=['state','target_model']
    readonly_fields=[field.name for field in EscalationRequest._meta.fields]
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False
