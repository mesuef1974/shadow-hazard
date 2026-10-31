"""One message ledger backed by the existing authorized job/outbox pipeline."""
import uuid
from django.db import transaction
from .models import SessionMessage


def register_message(job):
    if job.mode!='workspace-write':return None
    key=job.submission_key or uuid.uuid5(uuid.NAMESPACE_URL,f'shadow-job:{job.pk}')
    message,created=SessionMessage.objects.get_or_create(job=job,defaults={
        'message_key':key,'correlation_key':key,
        'recipient':job.charter,'body':job.prompt})
    return message


@transaction.atomic
def send_message(*,sender,recipient,owner,body,message_key,kind='task',reply_to=None,task_kind='auto'):
    from workspace.dispatcher import enqueue
    key=uuid.UUID(str(message_key))
    if not owner.is_active or owner.role!='supervisor' or not recipient.policy.enabled:
        raise ValueError('إنشاء الرسالة يتطلب مشرفًا نشطًا وجلسة مفعلة')
    if kind not in dict(SessionMessage._meta.get_field('kind').choices):
        raise ValueError('نوع الرسالة غير صالح')
    if not sender or sender.project_id!=recipient.project_id or not sender.policy.enabled:
        raise ValueError('جلسة المرسل غير صالحة أو من مشروع مختلف')
    if any(session.state not in {'active','prepared','ready_waiting_for_task'} or not session.thread_id for session in (sender,recipient)):
        raise ValueError('إحدى الجلسات غير مهيأة للمراسلة')
    if reply_to and reply_to.recipient.project_id!=recipient.project_id:
        raise ValueError('الرد خارج المشروع')
    body=body.strip()
    expected=(sender.pk,recipient.pk,body,kind,reply_to.pk if reply_to else None)
    previous=SessionMessage.objects.filter(message_key=key).first()
    if previous:
        actual=(previous.sender_id,previous.recipient_id,previous.body,previous.kind,previous.reply_to_id)
        if actual!=expected or previous.job.owner_id!=owner.pk:
            raise ValueError('معرّف الرسالة مستخدم بمحتوى مختلف')
        if task_kind!='auto' and previous.job.task_context.kind!=task_kind:
            raise ValueError('معرّف الرسالة مستخدم بنوع مهمة مختلف')
        return previous
    job=enqueue(recipient,owner,body,mode='workspace-write',submission_key=key,
        task_kind='text' if kind in {'reply','notice'} else task_kind)
    register_message(job)
    message=SessionMessage.objects.select_for_update().get(job=job)
    actual=(message.sender_id,message.recipient_id,message.body,message.kind,message.reply_to_id)
    if message.sender_id and actual!=expected:
        raise ValueError('حُجز معرّف الرسالة المتزامن بمحتوى مختلف')
    message.sender=sender;message.kind=kind;message.reply_to=reply_to
    message.correlation_key=reply_to.correlation_key if reply_to else key
    message.full_clean();message.save()
    return message
