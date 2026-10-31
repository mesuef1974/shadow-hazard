"""Record context inclusion, without claiming that a model invoked a connector."""
import hashlib
from pathlib import Path
from django.utils import timezone
from django.db import transaction
from registry.models import Actor, ExecutionSession, RegistryTask, Service, ServiceVersion
from registry.services import record_usage


@transaction.atomic
def record_local_execution(job,plan):
    actor,_=Actor.objects.get_or_create(key='local-model-dispatcher',defaults={'name':'موزع المهام المحلي','kind':'agent'})
    session,_=ExecutionSession.objects.get_or_create(external_key=f'local-calculation-job-{job.pk}',defaults={
        'name':f'حساب محلي · طلب {job.pk}','actor':actor,'client':'Python Decimal','model':'none'})
    task,_=RegistryTask.objects.get_or_create(key=f'delegation-job-{job.pk}',defaults={
        'title':f'طلب تفويض {job.pk}','purpose':job.prompt})
    service,_=Service.objects.get_or_create(key='delegation-local-calculator',defaults={
        'name':'حاسبة التفويض المحلية','kind':'tool','purpose':'عملية حسابية محددة دون نموذج أو eval'})
    path=Path(__file__).with_name('routing.py')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    version,_=ServiceVersion.objects.get_or_create(service=service,sha256=digest,defaults={
        'source':str(path),'version_label':'بصمة كود الحاسبة'})
    record_usage(event_key=f'delegation:{job.pk}:local-calculator',version=version,session=session,task=task,
        recorded_at=timezone.now(),used_at=job.started_at,purpose=job.prompt,
        method='Decimal مع تعبير مقيد إلى معاملين وعملية واحدة، دون استدعاء نموذج',
        result=job.result,time_evidence=f'ModelJob={job.pk}; TaskContext={plan.pk}',status='completed')


@transaction.atomic
def record_context_load(job,plan,thread_id):
    if not plan or not (plan.skills or plan.plugins):return
    actor,_=Actor.objects.get_or_create(key='local-model-dispatcher',defaults={'name':'موزع المهام المحلي','kind':'agent'})
    session,_=ExecutionSession.objects.get_or_create(external_key=thread_id,defaults={
        'name':f'{job.charter.name} · طلب {job.pk}','actor':actor,'client':'Codex CLI dispatcher','model':job.requested_model})
    task,_=RegistryTask.objects.get_or_create(key=f'delegation-job-{job.pk}',defaults={
        'title':f'طلب تفويض {job.pk}','purpose':job.prompt})
    entries=[('skill',entry['skill'],entry['sha256'],entry['path']) for entry in plan.manifest['files'] if entry.get('skill')]
    entries += [('tool',name,hashlib.sha256(('plugin-config:'+name).encode()).hexdigest(),'configured-plugin:'+name) for name in plan.plugins]
    for kind,name,digest,source in entries:
        version=ServiceVersion.objects.filter(service__kind=kind,sha256=digest).first()
        if version is None:
            service,_=Service.objects.get_or_create(key='delegation-'+kind+':'+name,defaults={
                'name':name,'kind':kind,'purpose':'عنصر سياق مطلوب لمهمة تفويض'})
            version,_=ServiceVersion.objects.get_or_create(service=service,sha256=digest,defaults={
                'source':source,'version_label':'بصمة تعليمات' if kind=='skill' else 'مرجع إعداد — ليس إصدار البرنامج',
                'metadata':{'evidence_kind':'skill_file' if kind=='skill' else 'configuration_reference_not_binary_version'}})
        event_id=hashlib.sha256((kind+':'+name).encode()).hexdigest()[:24]
        record_usage(event_key=f'delegation:{job.pk}:{event_id}',version=version,session=session,task=task,
            recorded_at=timezone.now(),purpose=job.prompt,
            method='تضمين تعليمات المهارة في الطلب' if kind=='skill' else 'طلب تفعيل الموصل المحدد لهذه المهمة فقط',
            result='سجل تحميل السياق؛ لا يثبت تنفيذ السكربتات أو استدعاء الموصل فعليًا.',
            time_evidence=f'thread.started={thread_id}; TaskContext={plan.pk}',status='applied_to_draft')
