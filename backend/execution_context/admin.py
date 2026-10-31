from django.contrib import admin
from .models import TaskContext, EscalationRequest, SessionDispatch, SessionDispatchEvent, SessionDispatchTurn, SessionRoutingPolicy
from .models import SessionMessage
from django.urls import reverse
from django.utils.html import format_html


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


@admin.register(SessionRoutingPolicy)
class SessionRoutingPolicyAdmin(admin.ModelAdmin):
    list_display=['category','model','thinking','stuck_model','stuck_thinking','max_attempts','max_escalations','enabled']
    list_filter=['enabled','category']


@admin.register(SessionDispatch)
class SessionDispatchAdmin(admin.ModelAdmin):
    list_display=['id','job','category','target_charter','requested_model','requested_thinking','attempt_no','state','created_at']
    list_filter=['state','category','requested_model']
    search_fields=['job__prompt','target_thread_id','dispatch_key']
    readonly_fields=[field.name for field in SessionDispatch._meta.fields]
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False


@admin.register(SessionDispatchEvent)
class SessionDispatchEventAdmin(admin.ModelAdmin):
    list_display=['dispatch','state','created_at']
    readonly_fields=[field.name for field in SessionDispatchEvent._meta.fields]
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False


@admin.register(SessionDispatchTurn)
class SessionDispatchTurnAdmin(admin.ModelAdmin):
    list_display=['dispatch','target_thread_id','turn_key','model','thinking','created_at']
    search_fields=['turn_key','target_thread_id']
    readonly_fields=[field.name for field in SessionDispatchTurn._meta.fields]
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False


@admin.register(SessionMessage)
class SessionMessageAdmin(TaskContextAdmin):
    list_display=['id','sender_label','recipient','kind','delivery_state','job','created_at']
    list_filter=['kind','sender','recipient','job__session_dispatch__state']
    search_fields=['body','message_key','correlation_key','job__id','job__session_dispatch__target_thread_id']
    readonly_fields=[field.name for field in SessionMessage._meta.fields]+['delivery_state','delivery_times','delivery_evidence']
    list_select_related=['sender','recipient','job','job__session_dispatch']

    @admin.display(description='المرسل')
    def sender_label(self,obj):
        return obj.sender.name if obj.sender_id else f'مالك الطلب — {obj.job.owner.username}'

    @admin.display(description='حالة الإرسال')
    def delivery_state(self,obj):
        return {'queued':'في الطابور','claimed':'محجوز للإرسال','sent':'قبله طابور التطبيق','acknowledged':'وصل بدليل','completed':'مكتمل بدليل','uncertain':'غير محسوم'}.get(obj.state,obj.state)

    @admin.display(description='أوقات الإرسال والاستلام والاكتمال')
    def delivery_times(self,obj):
        d=obj.delivery
        return f'{d.sent_at} / {d.acknowledged_at} / {d.completed_at}' if d else 'لم يجهز صندوق الإرسال بعد'

    @admin.display(description='مرجع الأدلة والاستهلاك')
    def delivery_evidence(self,obj):
        d=obj.delivery
        return format_html('<a href="{}">سجل الإرسال #{} والأحداث وأدوار التنفيذ</a>',reverse('admin:execution_context_sessiondispatch_change',args=[d.pk]),d.pk) if d else 'لا دليل تنفيذ بعد'
