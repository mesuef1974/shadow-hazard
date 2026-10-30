from django.core.exceptions import ValidationError
from django.db import transaction
from .models import UsageEvent, RegistryEvent

@transaction.atomic
def record_usage(*, event_key, version, session, task, recorded_at, purpose, method, result, time_evidence, installation=None, used_at=None, status="applied_to_draft"):
    if installation and installation.version_id != version.id and not UsageEvent.objects.filter(event_key=event_key, installation=installation, version=version).exists():
        raise ValidationError("Installation and service version must match")
    values = dict(version=version, session=session, task=task, recorded_at=recorded_at, purpose=purpose, method=method, result=result, time_evidence=time_evidence, installation=installation, used_at=used_at, status=status)
    event, created = UsageEvent.objects.get_or_create(event_key=event_key, defaults=values)
    if not created:
        for key in ["version","session","task","purpose","method","result","installation","used_at","status"]:
            if getattr(event,key) != values[key]:
                raise ValidationError("Event key conflicts with previously recorded usage")
    else:
        RegistryEvent.objects.create(key="usage:"+event_key, session=session, action="usage.recorded", summary={"usage_id":str(event.id),"task":task.key,"service":version.service.key})
    return event, created
