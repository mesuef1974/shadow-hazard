from django.db import models


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
