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
    from operations.operator_usage import collect
    from workspace.models import Project
    for project in Project.objects.all():
        try:collect(project)
        except Exception as exc:
            result['errors'].append({'operation':'operator_usage_comparison','error_type':type(exc).__name__})
    return result


def auto_renewal_cycle():
    """Consume only after capacity maintenance has persisted its handovers."""
    try:
        from workspace.auto_renewal import maintenance
        return maintenance()
    except Exception as exc:
        return {'latched': 0, 'ready': 0, 'advanced': 0,
            'errors': [{'operation': 'auto_renewal', 'error_type': type(exc).__name__}]}
