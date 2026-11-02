from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError


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


class SessionRoutingPolicy(models.Model):
    category=models.CharField(max_length=30,unique=True)
    model=models.CharField(max_length=80)
    thinking=models.CharField(max_length=20)
    stuck_model=models.CharField(max_length=80,blank=True)
    stuck_thinking=models.CharField(max_length=20,blank=True)
    max_attempts=models.PositiveSmallIntegerField(default=1)
    max_escalations=models.PositiveSmallIntegerField(default=0)
    enabled=models.BooleanField(default=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(max_attempts__gte=1),name='routing_policy_attempts_positive')]
        verbose_name='سياسة توجيه الجلسة'
        verbose_name_plural='سياسات توجيه الجلسات'
    def clean(self):
        if self.model not in {'gpt-6-luna','gpt-6.1-sol','gpt-6-astra'} or self.thinking not in {'low','medium','high'}:
            raise ValidationError('نموذج أو مستوى تفكير غير مسموح في سياسة التوجيه')
        if self.stuck_model and (self.stuck_model not in {'gpt-6-luna','gpt-6.1-sol','gpt-6-astra'} or self.stuck_thinking not in {'low','medium','high'}):
            raise ValidationError('سياسة التعثر تحتوي نموذجًا أو مستوى تفكير غير مسموح')
        if self.max_escalations and not self.stuck_model:
            raise ValidationError('حد التصعيد يتطلب نموذجًا محددًا للتعثر')


class SessionDispatch(models.Model):
    """Durable outbox record; delivery is performed by an authorized Codex bridge."""
    STATES=[('queued','queued'),('claimed','claimed'),('sent','sent'),('acknowledged','acknowledged'),('completed','completed'),('finished_review','انتهى الدور؛ يحتاج مراجعة السياسة'),('evidence_rejected','انتهى الدور؛ الدليل مرفوض'),('uncertain','uncertain')]
    job=models.OneToOneField('workspace.ModelJob',on_delete=models.PROTECT,related_name='session_dispatch')
    dispatch_key=models.UUIDField(unique=True,editable=False)
    target_charter=models.ForeignKey('workspace.SessionCharter',on_delete=models.PROTECT)
    target_thread_id=models.CharField(max_length=100)
    category=models.CharField(max_length=30)
    routing_policy=models.ForeignKey(SessionRoutingPolicy,on_delete=models.PROTECT,null=True)
    attempt_no=models.PositiveSmallIntegerField(default=1)
    max_attempts=models.PositiveSmallIntegerField(default=1)
    escalation_count=models.PositiveSmallIntegerField(default=0)
    requested_model=models.CharField(max_length=80)
    requested_thinking=models.CharField(max_length=20)
    route_reason=models.TextField()
    state=models.CharField(max_length=20,choices=STATES,default='queued')
    created_at=models.DateTimeField(auto_now_add=True)
    claim_token=models.UUIDField(null=True,unique=True)
    claimed_by=models.CharField(max_length=100,blank=True)
    claimed_at=models.DateTimeField(null=True)
    updated_at=models.DateTimeField(auto_now=True)
    sent_at=models.DateTimeField(null=True)
    acknowledged_at=models.DateTimeField(null=True)
    completed_at=models.DateTimeField(null=True)
    turn_key=models.CharField(max_length=120,blank=True)
    class Meta:
        indexes=[models.Index(fields=['state','created_at'],name='dispatch_state_created')]
        ordering=['created_at','pk']


class SessionDispatchEvent(models.Model):
    dispatch=models.ForeignKey(SessionDispatch,on_delete=models.PROTECT,related_name='events')
    state=models.CharField(max_length=20)
    detail=models.JSONField(default=dict,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['created_at','pk']


class SessionDispatchTurn(models.Model):
    dispatch=models.ForeignKey(SessionDispatch,on_delete=models.PROTECT,related_name='turns')
    target_thread_id=models.CharField(max_length=100,null=True)
    turn_key=models.CharField(max_length=120)
    model=models.CharField(max_length=80,blank=True)
    thinking=models.CharField(max_length=20,blank=True)
    source_sample_ids=models.JSONField(default=list,blank=True)
    usage=models.JSONField(default=dict,blank=True)
    source_evidence=models.JSONField(default=dict,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['target_thread_id','turn_key'],name='global_session_turn_once')]


class SessionWriterRecovery(models.Model):
    dispatch = models.OneToOneField(SessionDispatch, on_delete=models.PROTECT, related_name='writer_recovery')
    thread_id = models.CharField(max_length=100)
    state = models.CharField(max_length=20, choices=[
        ('archiving', 'جارٍ تحرير الحجز'), ('restoring', 'جارٍ استعادة الجلسة'),
        ('released', 'حُرر الحجز واستعيدت الجلسة'), ('blocked', 'تعذر التحرير الآمن'),
        ('uncertain', 'الاستعادة غير مؤكدة'),
    ])
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        verbose_name = 'استعادة حجز جلسة'
        verbose_name_plural = 'استعادات حجوزات الجلسات'


class AuthorityTransferJournal(models.Model):
    """Durable CAS journal for a separately authorized owner-channel transfer."""
    STATES=[('prepared','prepared'),('idle_verified','idle_verified'),('authorized','authorized'),
        ('frozen','frozen'),('config_written','config_written'),('db_committed','db_committed'),
        ('complete','complete'),('ambiguous','ambiguous'),('rolled_back','rolled_back'),('blocked','blocked')]
    key=models.CharField(max_length=64,unique=True)
    project=models.ForeignKey('workspace.Project',on_delete=models.PROTECT)
    source=models.ForeignKey('workspace.SessionCharter',on_delete=models.PROTECT,related_name='+')
    target=models.ForeignKey('workspace.SessionCharter',on_delete=models.PROTECT,related_name='+')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    preparation=models.ForeignKey('workspace.ProjectDocumentation',on_delete=models.PROTECT,related_name='+')
    authorization=models.ForeignKey('workspace.ProjectDocumentation',null=True,on_delete=models.PROTECT,related_name='+')
    idle_evidence=models.JSONField(default=dict)
    before_snapshot=models.JSONField(default=dict)
    after_snapshot=models.JSONField(default=dict)
    old_config_sha256=models.CharField(max_length=64)
    new_config_sha256=models.CharField(max_length=64,blank=True)
    state=models.CharField(max_length=24,choices=STATES,default='prepared')
    execution_enabled=models.BooleanField(default=False)
    lease_key=models.CharField(max_length=64,blank=True)
    rollback_authorization=models.ForeignKey('workspace.ProjectDocumentation',null=True,on_delete=models.PROTECT,related_name='+')
    transferred_at=models.DateTimeField(null=True)
    error_code=models.CharField(max_length=80,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)

    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(source__isnull=False,target__isnull=False),name='authority_journal_bindings_present')]


class SessionMessage(models.Model):
    """Message metadata; delivery lifecycle belongs exclusively to the job outbox."""
    message_key=models.UUIDField('معرّف منع التكرار',unique=True,editable=False)
    correlation_key=models.UUIDField('معرّف الارتباط',db_index=True)
    job=models.OneToOneField('workspace.ModelJob',on_delete=models.PROTECT,related_name='session_message',verbose_name='المهمة')
    sender=models.ForeignKey('workspace.SessionCharter',on_delete=models.PROTECT,null=True,blank=True,related_name='outgoing_messages',verbose_name='الجلسة المرسلة')
    recipient=models.ForeignKey('workspace.SessionCharter',on_delete=models.PROTECT,related_name='incoming_messages',verbose_name='الجلسة المستقبلة')
    kind=models.CharField('نوع الرسالة',max_length=24,choices=[('task','مهمة'),('reply','رد'),('handoff','تسليم'),('notice','إشعار')],default='task')
    body=models.TextField('المحتوى')
    reply_to=models.ForeignKey('self',on_delete=models.PROTECT,null=True,blank=True,related_name='replies',verbose_name='رد على')
    created_at=models.DateTimeField('وقت التسجيل',auto_now_add=True)

    @property
    def delivery(self):
        return getattr(self.job,'session_dispatch',None)

    @property
    def state(self):
        return self.delivery.state if self.delivery else self.job.state

    def clean(self):
        if self.recipient_id!=self.job.charter_id or self.body!=self.job.prompt:
            raise ValidationError('المستقبل والمحتوى يجب أن يطابقا المهمة الأصلية')
        if self.sender_id and self.sender.project_id!=self.recipient.project_id:
            raise ValidationError('لا رسائل بين مشاريع مختلفة')
        if self.reply_to_id and self.reply_to.recipient.project_id!=self.recipient.project_id:
            raise ValidationError('الرد يجب أن يكون داخل المشروع نفسه')

    class Meta:
        verbose_name='رسالة جلسة'
        verbose_name_plural='رسائل الجلسات'
        ordering=['-created_at','-pk']

    def __str__(self):
        return f'رسالة {self.pk} — {self.recipient.name} — طلب {self.job_id}'
