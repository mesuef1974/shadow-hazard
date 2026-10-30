import hashlib
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from registry.models import Service, ServiceVersion, ExecutionSession, RegistryTask, FailureRecord
from registry.services import record_usage
class Command(BaseCommand):
    help = "Record a service observation from a reviewed JSON envelope; no automatic external hooks."
    def add_arguments(self, parser): parser.add_argument("envelope")
    @transaction.atomic
    def handle(self,*args,**opts):
        data=json.loads(Path(opts["envelope"]).read_text(encoding="utf-8-sig"))
        session=ExecutionSession.objects.get(external_key=data["session_id"])
        task=RegistryTask.objects.get(key=data["task_id"])
        for row in data["events"]:
            service,_=Service.objects.get_or_create(key=row["service_key"],defaults={"name":row["name"],"kind":row["kind"],"purpose":row["purpose"]})
            spec=row["version"]
            h=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
            version,_=ServiceVersion.objects.get_or_create(service=service,sha256=h,defaults={"version_label":spec["label"],"source":spec["source"],"metadata":{"fingerprint_kind":"version_descriptor","provider_binary_hash_verified":False}})
            usage,created=record_usage(event_key=row["event_id"],version=version,session=session,task=task,recorded_at=timezone.now(),purpose=row["purpose"],method=row["method"],result=row["result"],status=row["status"],time_evidence="Observed in current task; exact execution start timestamp unavailable")
            if row.get("failure"):
                fail=row["failure"]
                FailureRecord.objects.get_or_create(key=row["event_id"],defaults={"task":task,"session":session,**fail})
        self.stdout.write("Recorded reviewed service observations.")
