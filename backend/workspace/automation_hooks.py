"""Token-free maintenance invoked by the single local dispatcher."""
from django.apps import apps


def automation_cycle():
    if not apps.is_installed('operations'):
        return {'enabled':False,'errors':[]}
    from operations.services import audit_automation,run_due_backup
    from operations.review_assignment import assign_pending_reviews
    result={'enabled':True,'errors':[]}
    for name,operation in [('audit',audit_automation),('reviews',assign_pending_reviews),('backup',run_due_backup)]:
        try:
            result[name]=operation()
        except Exception as exc:
            # Keep dispatching independent tasks. Failure remains visible in health.
            result['errors'].append({'operation':name,'error_type':type(exc).__name__})
    return result
