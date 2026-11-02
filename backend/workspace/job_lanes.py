"""Keep uncertain or active work from blocking unrelated Codex sessions."""
from django.db.models import F, Q


ACTIVE_JOB_STATES = ('running', 'uncertain')
ACTIVE_DISPATCH_STATES = ('claimed', 'sent', 'acknowledged', 'uncertain')


def blocking_dispatches():
    from execution_context.models import SessionDispatch
    # A received but unverified historical task remains quarantined on its old
    # conversation. Rotation must not transfer that reservation to the new one.
    retired_review=Q(state='acknowledged',job__state='needs_review') & ~Q(
        target_thread_id=F('target_charter__thread_id'))
    return SessionDispatch.objects.filter(state__in=ACTIVE_DISPATCH_STATES).exclude(retired_review)


def available_jobs(query):
    """Return queued ModelJobs whose session lane has no unresolved work."""
    from execution_context.models import SessionDispatch
    from .models import ModelJob, SessionRenewal
    from .renewal import GATING_STATES

    busy_jobs = ModelJob.objects.filter(state__in=ACTIVE_JOB_STATES).values('lane')
    busy_dispatches = blocking_dispatches().values('job__lane')
    reserved = SessionRenewal.objects.filter(state__in=GATING_STATES).values('charter_id')
    return query.exclude(lane__in=busy_jobs).exclude(lane__in=busy_dispatches).exclude(
        Q(lane__in=reserved) | Q(charter_id__in=reserved))


def available_dispatches(query):
    """Return queued dispatches not blocked by active work in the same lane."""
    from execution_context.models import SessionDispatch
    from .models import ModelJob

    busy_jobs = ModelJob.objects.filter(state__in=ACTIVE_JOB_STATES).values('lane')
    busy_dispatches = blocking_dispatches().values('job__lane')
    return without_renewing_dispatches(query.exclude(job__lane__in=busy_jobs).exclude(job__lane__in=busy_dispatches))


def without_renewing_dispatches(query):
    from .models import SessionRenewal
    from .renewal import GATING_STATES
    reserved = SessionRenewal.objects.filter(state__in=GATING_STATES).values('charter_id')
    return query.exclude(
        Q(job__lane__in=reserved) | Q(target_charter_id__in=reserved) | Q(job__charter_id__in=reserved))
