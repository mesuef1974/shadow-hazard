from io import StringIO
from django.test import TestCase, RequestFactory
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from accounts.models import User
from registry.models import *
from registry.services import record_usage
from registry.admin import ReadOnlyRegistryAdmin
from django.contrib.admin.sites import AdminSite

class RegistryIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_project_registry", stdout=StringIO())
    def test_reimport_is_idempotent_and_keeps_usage_evidence(self):
        before=(Installation.objects.count(), UsageEvent.objects.count(), BrandColor.objects.count(), RegistryEvent.objects.count(), Artifact.objects.count())
        call_command("import_project_registry", stdout=StringIO())
        self.assertEqual(before,(Installation.objects.count(), UsageEvent.objects.count(), BrandColor.objects.count(), RegistryEvent.objects.count(), Artifact.objects.count()))
        self.assertEqual(Installation.objects.count(),124)
    def test_brand_is_draft_and_approval_requires_evidence(self):
        self.assertEqual(BrandDirection.objects.filter(status="draft").count(),3)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BrandDirection.objects.filter(code="sightline").update(status="approved")
    def test_invalid_color_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            BrandColor.objects.create(direction=BrandDirection.objects.first(),token="bad",name="bad",hex_value="orange")
    def test_fourth_account_and_student_escalation_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(username="fourth",role="student")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(username="saud",role="student",is_superuser=True)
    def test_installation_version_mismatch_rejected(self):
        old=UsageEvent.objects.filter(installation__isnull=False).first()
        other=ServiceVersion.objects.exclude(id=old.version_id).first()
        with self.assertRaises(ValidationError):
            record_usage(event_key="bad-installation",version=other,session=old.session,task=old.task,recorded_at=timezone.now(),purpose="x",method="x",result="x",time_evidence="x",installation=old.installation)
        self.assertFalse(UsageEvent.objects.filter(event_key="bad-installation").exists())
    def test_usage_replay_cannot_rewrite_history(self):
        old=UsageEvent.objects.first()
        with self.assertRaises(ValidationError):
            record_usage(event_key=old.event_key,version=old.version,session=old.session,task=old.task,recorded_at=old.recorded_at,purpose="modified",method=old.method,result=old.result,time_evidence=old.time_evidence,installation=old.installation)
        self.assertNotEqual(UsageEvent.objects.get(pk=old.pk).purpose,"modified")
    def test_student_has_no_registry_admin_access(self):
        user=User.objects.create(username="saud",role="student")
        self.client.force_login(user)
        response=self.client.get("/admin/registry/service/")
        self.assertEqual(response.status_code,302)
        self.assertIn("/admin/login/",response.url)
    def test_admin_registry_cannot_mutate_usage(self):
        user=User.objects.create(username="sufyan",role="supervisor",is_staff=True,is_superuser=True)
        request=RequestFactory().get("/admin/");request.user=user
        admin=ReadOnlyRegistryAdmin(UsageEvent,AdminSite())
        self.assertTrue(admin.has_view_permission(request))
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request))
        self.assertFalse(admin.has_delete_permission(request))

    def test_observation_command_is_idempotent(self):
        from pathlib import Path
        envelope=Path(__file__).resolve().parents[2]/"docs/registry/runtime-events.json"
        call_command("record_service_usage", str(envelope), stdout=StringIO())
        before=(UsageEvent.objects.count(),RegistryEvent.objects.count(),FailureRecord.objects.count())
        call_command("record_service_usage", str(envelope), stdout=StringIO())
        self.assertEqual(before,(UsageEvent.objects.count(),RegistryEvent.objects.count(),FailureRecord.objects.count()))
