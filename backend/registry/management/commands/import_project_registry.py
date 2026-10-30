import hashlib
import json
import mimetypes
from pathlib import Path
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from registry.models import *
from registry.services import record_usage

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

class Command(BaseCommand):
    help = "Import verified inventory, observed usages, and draft brand proposals atomically."
    def add_arguments(self, parser):
        parser.add_argument("--root", default=str(Path(__file__).resolve().parents[4]))
    @transaction.atomic
    def handle(self, *args, **opts):
        root=Path(opts["root"]).resolve()
        inv_path=root/"docs/registry/skills-inventory.json"
        inv=json.loads(inv_path.read_text(encoding="utf-8"))
        uses=json.loads((root/"docs/registry/skill-usage.json").read_text(encoding="utf-8"))
        brands=json.loads((root/"docs/brand/directions.json").read_text(encoding="utf-8"))["directions"]
        fonts=json.loads((root/"docs/brand/font-sources.json").read_text(encoding="utf-8"))
        actor,_=Actor.objects.get_or_create(key="codex-assistant",defaults={"name":"Codex assistant","kind":"agent"})
        session,_=ExecutionSession.objects.get_or_create(external_key="01a18e02-0487-7180-9aa0-f932b22c8ef4",defaults={"name":"اقتراح الهوية وجرد المهارات","actor":actor,"client":"Codex desktop","model":"gpt-6-astra (session declared)"})
        task,_=RegistryTask.objects.get_or_create(key="brand-directions-and-capability-inventory",defaults={"title":"ثلاثة اتجاهات للهوية وجرد المهارات","purpose":"مقارنة بصرية وسجل استخدام فعلي ونواة Django؛ الهوية بانتظار اعتماد سفيان"})
        for row in inv["installations"]:
            service,_=Service.objects.get_or_create(key=row.get("service_key", "skill:"+row["name"]),defaults={"name":row["name"],"kind":"skill","purpose":row["description"] or ""})
            version,_=ServiceVersion.objects.get_or_create(service=service,sha256=row["sha256"],defaults={"version_label":row["sha256"][:12],"source":row["root_alias"]+"/"+row["relative_path"],"metadata":{"commercial_footer_detected":row["commercial_footer_detected"]}})
            Installation.objects.get_or_create(external_key=row["installation_id"],defaults={"version":version,"root_alias":row["root_alias"],"relative_path":row["relative_path"],"observed_at":datetime.fromisoformat(row["observed_at"]),"presence":row["presence"],"client_enabled":row["client_enabled"]})
        def artifact(rel):
            path=(root/rel).resolve()
            if not path.is_relative_to(root) or not path.is_file(): raise CommandError("Missing or invalid artifact: "+rel)
            obj,_=Artifact.objects.get_or_create(path=rel,sha256=digest(path),defaults={"media_type":mimetypes.guess_type(rel)[0] or "application/octet-stream"})
            return obj
        for row in uses:
            install=Installation.objects.select_related("version__service").get(external_key=row["installation_id"])
            historical_version=ServiceVersion.objects.get(service=install.version.service,sha256=row["skill_sha256"])
            event,created=record_usage(event_key=row["event_id"],version=historical_version,installation=install,session=session,task=task,recorded_at=datetime.fromisoformat(row["recorded_at"]),purpose=row["purpose"],method=row["method"],result="Applied to three draft directions and local review assets; no brand approved.",time_evidence=row["time_evidence"])
            if created:
                for rel in row["outputs"]: UsageArtifact.objects.get_or_create(usage=event,artifact=artifact(rel))
            Service.objects.filter(pk=install.version.service_id,state="discovered").update(state="used_in_draft")
        font_versions={}
        for row in fonts:
            service,_=Service.objects.get_or_create(key="font:"+row["family"],defaults={"name":row["family"],"kind":"font","purpose":"Arabic/English identity specimen; local hosting"})
            path=root/"docs/brand/fonts"/row["filename"]
            if digest(path)!=row["sha256"]:raise CommandError("Font fingerprint mismatch")
            version,_=ServiceVersion.objects.get_or_create(service=service,sha256=row["sha256"],defaults={"version_label":row["filename"],"source":row["source_url"],"license_note":"OFL file preserved with downloaded family","metadata":{"local_path":path.relative_to(root).as_posix()}})
            if path.suffix==".ttf" and "Italic" not in path.name:font_versions.setdefault(row["family"],version)
            e,created=record_usage(event_key="font:"+row["sha256"][:40]+":"+row["family"],version=version,session=session,task=task,recorded_at=timezone.now(),purpose="عرض الخط محليًا مع حفظ ترخيصه ومصدره",method="Download official font file and retain source fingerprint",result="Bundled in review page; no system font installation.",time_evidence="Registration time; exact download time retained in font manifest")
            if created:UsageArtifact.objects.get_or_create(usage=e,artifact=artifact(path.relative_to(root).as_posix()))
        for row in brands:
            direction,_=BrandDirection.objects.get_or_create(code=row["code"],defaults={"name":row["name"],"english_name":row["english"],"story":row["idea"],"mascot_name":row["mascot"],"mascot_description":row["character"],"recommendation":row["fit"] if row["recommended"] else "","status":"draft"})
            # Import never overwrites approved identity records.
            for token,name,value in row["colors"]:
                color,_=BrandColor.objects.get_or_create(direction=direction,token=token,defaults={"name":name,"hex_value":value})
                if color.hex_value!=value:raise CommandError("Brand token changed; create an explicit revision")
            for script,family,label in [("ar",row["family_ar"],row["arabic"]),("en",row["family_en"],row["latin"])]:
                BrandTypography.objects.get_or_create(direction=direction,script=script,defaults={"family":label,"font_version":font_versions[family]})
            BrandAsset.objects.get_or_create(direction=direction,artifact=artifact("docs/brand/concept-board.png"),kind="generated_concept_board",defaults={"usage_rule":"مسودة مقارنة؛ ليست ملفات شعار نهائية أو تصميمًا ثلاثي الأبعاد جاهزًا"})
        first=BrandDirection.objects.get(code="sightline")
        BrandAsset.objects.get_or_create(direction=first,artifact=artifact("docs/brand/sightline-symbol-draft.svg"),kind="draft_vector_symbol",defaults={"usage_rule":"رسم متجهي مبسط أولي؛ غير معتمد"})
        for key,name,kind,purpose,method,result,output in [
          ("imagegen","Image generation","tool","إنشاء لوحة اتجاهات للشعار والتميمة","Text-to-image prompt with project purpose and three palettes","Three-direction concept board generated; no trademark clearance claimed","docs/brand/concept-board.png"),
          ("django","Django registry","dependency","تخزين الجرد والاستخدامات والهوية علائقيًا","Django models and portable migrations","Local registry database; deployment and login activation deferred","backend/requirements.txt")]:
            a=artifact(output)
            service,_=Service.objects.get_or_create(key=kind+":"+key,defaults={"name":name,"kind":kind,"purpose":purpose})
            v,_=ServiceVersion.objects.get_or_create(service=service,sha256=a.sha256,defaults={"version_label":"observed-task-artifact","source":output,"metadata":{"fingerprint_kind":"output_artifact_not_provider_binary"}})
            e,created=record_usage(event_key="service:"+key+":"+a.sha256[:40],version=v,session=session,task=task,recorded_at=timezone.now(),purpose=purpose,method=method,result=result,time_evidence="Recorded during this task; exact provider start time unavailable")
            if created:UsageArtifact.objects.get_or_create(usage=e,artifact=a)
        FailureRecord.objects.get_or_create(key="design-query-off-target",defaults={"task":task,"session":session,"symptom":"Initial design-system search returned academic portfolio typography","cause":"Broad research query matched portfolio pattern; not a proven engine defect","correction":"Reran once with operations monitoring dashboard; applied relevant guidance selectively","prevention":"Check relevance and Arabic support before adopting generated design tokens","evidence":"Two ui-ux-pro-max search outputs in the current session"})
        RegistryEvent.objects.get_or_create(key="inventory:"+digest(inv_path),defaults={"session":session,"action":"inventory.imported","summary":{"installations":len(inv["installations"]),"brand_directions":len(brands),"identity_approved":False}})
        self.stdout.write(json.dumps({"installations":Installation.objects.count(),"services":Service.objects.count(),"usage_events":UsageEvent.objects.count(),"brand_directions":BrandDirection.objects.count(),"brand_colors":BrandColor.objects.count(),"users_activated":get_user_model().objects.filter(is_active=True).count()},ensure_ascii=False))
