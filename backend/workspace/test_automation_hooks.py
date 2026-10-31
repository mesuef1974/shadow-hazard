from unittest.mock import patch
from django.test import SimpleTestCase
from .automation_hooks import automation_cycle


class AutomationHookTests(SimpleTestCase):
    @patch('operations.review_assignment.assign_pending_reviews')
    @patch('operations.services.run_due_backup')
    @patch('operations.services.audit_automation')
    def test_order_and_no_model_calls(self,audit,backup,reviews):
        calls=[]
        audit.side_effect=lambda:calls.append('audit') or {'created':0}
        backup.side_effect=lambda:calls.append('backup') or {'due':False}
        reviews.side_effect=lambda:calls.append('reviews') or {'created_batches':0}
        result=automation_cycle()
        self.assertEqual(calls,['audit','reviews','backup'])
        self.assertEqual(result['errors'],[])
        self.assertFalse(result['backup']['due'])

    @patch('operations.review_assignment.assign_pending_reviews',return_value={})
    @patch('operations.services.run_due_backup',side_effect=ValueError('invalid backup'))
    @patch('operations.services.audit_automation',return_value={})
    def test_backup_failure_is_visible_without_crashing_dispatcher(self,audit,backup,reviews):
        result=automation_cycle()
        self.assertEqual(result['errors'],[{'operation':'backup','error_type':'ValueError'}])

    @patch('workspace.automation_hooks.apps.is_installed',return_value=False)
    def test_missing_app_does_not_attempt_maintenance(self,installed):
        self.assertFalse(automation_cycle()['enabled'])
