"""Record the owner's explicit v1 decision; run from project root with Django shell.

This is a release-specific, idempotent record, not a general approval endpoint.
No accounts, permissions, historical proposals or previous evidence are removed.
"""
import hashlib
import json
import mimetypes
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from registry.models import (
    Actor, Artifact, BrandAsset, BrandColor, BrandDirection, BrandTypography,
    ExecutionSession, Installation, RegistryEvent, RegistryTask, Service,
    ServiceVersion, UsageArtifact,
)
from registry.services import record_usage

ROOT = Path.cwd()
DEST = ROOT / "docs/brand/approved-v1"
DECISION = "تمام اعتمد هذه الهوية والشعار وتميمة راصد لو سمحت، جهز ملفات الشعار والتميمة بخلفية شفافة ونسخ SVG، ووثق الاعتماد والألوان والخطوط في قاعدة البيانات لتكون الهوية الموحدة لكل المشروع"
FILES = {
    "logo.png": "logo_png", "logo.svg": "logo_svg",
    "logo-navy.svg": "logo_svg", "logo-white.svg": "logo_svg",
    "rased.png": "mascot_png", "rased.svg": "mascot_svg",
    "tokens.css": "brand_tokens", "README.md": "brand_guidelines",
}
COLORS = {"primary": "#142D4E", "secondary": "#007F82", "accent": "#F4B544", "background": "#F4F7FB"}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

# Validate files before changing approval state.
inventory = [{"path": str((DEST / name).relative_to(ROOT)).replace('\\', '/'),
              "sha256": digest(DEST / name), "kind": kind}
             for name, kind in FILES.items()]

with transaction.atomic():
    direction = BrandDirection.objects.select_for_update().get(code="reveal-occlusion")
    owner = get_user_model().objects.get(username="mesuef", is_active=True, is_superuser=True)
    if BrandDirection.objects.filter(status="approved").exclude(pk=direction.pk).exists():
        raise ValueError("Another identity is approved; resolve its status explicitly")
    actual_colors = dict(BrandColor.objects.filter(direction=direction).values_list("token", "hex_value"))
    if actual_colors != COLORS:
        raise ValueError("Database colors do not match the approved palette")
    fonts = list(BrandTypography.objects.filter(direction=direction).select_related("font_version__service"))
    if {f.script: f.family for f in fonts} != {"ar": "IBM Plex Sans Arabic", "en": "IBM Plex Sans"}:
        raise ValueError("Database fonts do not match the approved typography")
    session = ExecutionSession.objects.get(external_key="01a18e02-0487-7180-9aa0-f932b22c8ef4")
    task, _ = RegistryTask.objects.get_or_create(key="brand-approved-v1", defaults={
        "title": "تسليم الهوية المعتمدة والشعار وتميمة راصد",
        "purpose": "توحيد الهوية مع أصول شفافة ومتجهية وتوثيق الاعتماد والألوان والخطوط"})
    actor, _ = Actor.objects.get_or_create(user=owner, defaults={"key": "human:mesuef", "name": "سفيان", "kind": "human"})
    now = timezone.now()
    approval, created = RegistryEvent.objects.get_or_create(key="brand-identity-v1-approved", defaults={
        "session": session, "action": "brand.identity.approved", "summary": {
            "direction": direction.code, "release": "1.0", "approved_by": owner.username,
            "approver_actor_id": str(actor.pk), "recorded_by": session.actor.key,
            "decision_source": "explicit owner message in this conversation", "decision_text": DECISION,
            "recorded_at": now.isoformat(), "time_note": "وقت تسجيل الاعتماد؛ توقيت الرسالة الأصلي غير متاح",
            "files": inventory}})
    if not created and (approval.summary["decision_text"] != DECISION or approval.summary["files"] != inventory):
        raise ValueError("Immutable release evidence differs; create a new release")
    if direction.status != "approved":
        direction.status = "approved"
        direction.approved_by = owner
        direction.approved_at = now
        direction.recommendation = "الهوية الموحدة المعتمدة للمشروع؛ الإصدار 1.0"
        direction.full_clean()
        direction.save(update_fields=["status", "approved_by", "approved_at", "recommendation"])
    elif direction.approved_by_id != owner.pk:
        raise ValueError("Approval owner differs")
    manifest = {
        "release": "1.0", "direction": direction.code, "name": "ظل الخطر",
        "english_name": "Shadow Hazard", "mascot": "راصد", "status": "approved",
        "approval_event": approval.key, "approved_by": owner.username,
        "approval_recorded_at": direction.approved_at.isoformat(),
        "colors": actual_colors,
        "fonts": [{"script": f.script, "family": f.family, "version_id": str(f.font_version_id),
                   "sha256": f.font_version.sha256} for f in sorted(fonts, key=lambda x: x.script)],
        "assets": inventory,
        "vector_note": "SVG uses real paths; mascot shading quantized to 64 colors; PNG retains continuous shading",
    }
    (DEST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    active = {"direction": direction.code, "release": "1.0", "manifest": "approved-v1/manifest.json", "tokens": "approved-v1/tokens.css", "approval_event": approval.key}
    (DEST.parent / "active-identity.json").write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = inventory + [{"path": "docs/brand/approved-v1/manifest.json", "sha256": digest(DEST / "manifest.json"), "kind": "brand_manifest"}]
    artifacts = []
    for item in files:
        artifact, _ = Artifact.objects.get_or_create(path=item["path"], sha256=item["sha256"], defaults={"media_type": mimetypes.guess_type(item["path"])[0] or "application/octet-stream"})
        artifacts.append(artifact)
        rule = "الإصدار الموحد 1.0؛ حافظ على النسب والألوان ومساحة الحماية."
        if item["kind"] == "mascot_svg": rule += " إعادة بناء آلية بمسارات و64 لونًا؛ PNG مرجع التظليل الناعم."
        asset, _ = BrandAsset.objects.get_or_create(direction=direction, artifact=artifact, kind=item["kind"], defaults={"usage_rule": rule})
        asset.full_clean()
        service, _ = Service.objects.get_or_create(key="brand:v1:" + Path(item["path"]).name, defaults={"name": Path(item["path"]).name, "kind": "asset", "purpose": task.purpose, "state": "approved"})
        version, _ = ServiceVersion.objects.get_or_create(service=service, sha256=item["sha256"], defaults={"version_label": "1.0", "source": item["path"], "metadata": {"approval_event": approval.key}})
        usage, _ = record_usage(event_key="brand-v1-asset:" + Path(item["path"]).name, version=version, session=session, task=task, recorded_at=now, purpose=task.purpose,
            method="استخراج شفاف وإعادة بناء المسارات وتسجيل الأصل حسب نوعه", result=item["path"], time_evidence="Recorded after asset verification", status="completed")
        UsageArtifact.objects.get_or_create(usage=usage, artifact=artifact)
    # Record actual imagegen skill/tool usage with the installed version and evidence.
    skill = Installation.objects.filter(relative_path__endswith="imagegen/SKILL.md", presence="file_verified").first()
    if skill is None:
        raise ValueError("Imagegen skill must be inventoried before recording its use")
    usage, _ = record_usage(event_key="brand-v1-imagegen-skill", version=skill.version, installation=skill, session=session, task=task, recorded_at=now,
        purpose=task.purpose, method="استخدام مسار التعديل المدمج لاستخراج الشعار والتميمة بخلفية شفافة مع حفظ المرجع", result="أصول PNG وSVG محفوظة في approved-v1", time_evidence="Recorded after generation and review", status="completed")
    for artifact in artifacts: UsageArtifact.objects.get_or_create(usage=usage, artifact=artifact)
    for key, name, source in [("tool:imagegen", "Image generation", "image_gen.imagegen"), ("tool:brand-color-contour", "إعادة بناء مسارات الهوية", "tools/brand/trace-png.mjs")]:
        service, _ = Service.objects.get_or_create(key=key, defaults={"name": name, "kind": "tool", "purpose": task.purpose})
        fingerprint = digest(ROOT / source) if key.endswith("contour") else digest(DEST / "rased.png")
        version, _ = ServiceVersion.objects.get_or_create(service=service, sha256=fingerprint, defaults={"version_label": "brand-v1", "source": source, "metadata": {"hash_scope": "script" if key.endswith("contour") else "output artifact, not provider model"}})
        usage, _ = record_usage(event_key="brand-v1:" + key, version=version, session=session, task=task, recorded_at=now, purpose=task.purpose,
            method="استخراج الأصول ثم إعادة بناء المسارات والتحقق البصري والشفافية", result="approved-v1", time_evidence="Recorded after verification", status="completed")
        for artifact in artifacts: UsageArtifact.objects.get_or_create(usage=usage, artifact=artifact)
    for font in fonts:
        font.font_version.service.state = "approved"
        font.font_version.service.save(update_fields=["state"])
        usage, _ = record_usage(event_key="brand-v1-font:" + font.script, version=font.font_version, session=session, task=task, recorded_at=now,
            purpose="توحيد خط المنصة والمستندات", method="ربط ملفات الخط المحلية بمتغيرات CSS المركزية", result=font.family, time_evidence="Recorded after CSS preparation", status="completed")
        UsageArtifact.objects.get_or_create(usage=usage, artifact=artifacts[6])
print(json.dumps({"status": direction.status, "direction": direction.code, "colors": len(actual_colors), "fonts": len(fonts), "release_assets": len(artifacts), "approval_event": approval.key}, ensure_ascii=False))
