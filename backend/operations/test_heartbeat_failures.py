from django.test import TestCase
from django.utils import timezone
from workspace.models import DispatcherHeartbeat
from .models import AutomationIssue
from .services import audit_automation


class HeartbeatFailureTests(TestCase):
    def test_failed_worker_is_not_silently_ignored_or_duplicated(self):
        hb=DispatcherHeartbeat.objects.create(key='local',state='failed',last_error='failure')
        audit_automation();audit_automation()
        issue=AutomationIssue.objects.get(code='dispatcher_failure')
        self.assertEqual(issue.state,'open')
        hb.state='running';hb.last_error='';hb.updated_at=timezone.now();hb.save()
        audit_automation();issue.refresh_from_db()
        self.assertEqual(issue.state,'resolved')
