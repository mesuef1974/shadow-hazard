from django.db import models
from django.conf import settings


class TaskContext(models.Model):
    job=models.OneToOneField('workspace.ModelJob',on_delete=models.PROTECT,related_name='task_context')
    kind=models.CharField('نوع المهمة',max_length=30)
    skills=models.JSONField('المهارات المطلوبة',default=list)
    plugins=models.JSONField('الموصلات المطلوبة',default=list)
    instructions=models.TextField('نسخة تعليمات المشروع')
    charter=models.TextField('نسخة ميثاق الجلسة')
    skill_text=models.TextField('تعليمات المهارات المحددة',blank=True)
    manifest=models.JSONField('أدلة السياق',default=dict)
    created_at=models.DateTimeField(auto_now_add=True)
    @property
    def kind_label(self):
        from .routing import KINDS
        return dict(KINDS).get(self.kind,self.kind)
    class Meta:
        verbose_name='سياق مهمة'
        verbose_name_plural='سياقات المهام'


class EscalationRequest(models.Model):
    source_job=models.OneToOneField('workspace.ModelJob',on_delete=models.PROTECT,related_name='escalation_request')
    target_model=models.CharField(max_length=80)
    target_thinking=models.CharField(max_length=20,default='medium')
    reason=models.TextField('سبب التصعيد')
    proposed_by=models.CharField(max_length=20)
    snapshot=models.JSONField(default=dict)
    source_usage=models.JSONField(default=dict)
    state=models.CharField(max_length=20,default='pending')
    created_at=models.DateTimeField(auto_now_add=True)
    decided_at=models.DateTimeField(null=True)
    decided_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True)
    decision_note=models.TextField(blank=True)
    approved_job=models.OneToOneField('workspace.ModelJob',on_delete=models.PROTECT,null=True,related_name='approved_escalation')
    class Meta:
        verbose_name='طلب تصعيد نموذج'
        verbose_name_plural='طلبات تصعيد النماذج'
