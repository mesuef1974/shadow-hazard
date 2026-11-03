"""Direct per-job usage is a source, not an extra charge beside session samples."""
from dataclasses import dataclass
from .models import TokenSample, TrackedSession
from workspace.models import ModelJob


@dataclass(frozen=True)
class DirectUsageGroup:
    jobs: list
    token_usage: dict
    finished_at: object
    observed_model: str
    requested_thinking: str

    @property
    def pk(self):
        return self.jobs[0].pk if len(self.jobs) == 1 else None


def job_index(project):
    grouped={}
    for job in ModelJob.objects.filter(
        charter__project=project,execution_thread_id__gt='',finished_at__isnull=False
    ).filter(session_dispatch__isnull=True).exclude(token_usage={}).only(
        'pk','execution_thread_id','finished_at','token_usage','observed_model','requested_thinking').order_by('pk'):
        grouped.setdefault(job.execution_thread_id,[]).append(job)
    return grouped


def effective_samples(session, jobs, generation=None):
    if generation is None:
        from .models import UsageSourceState
        generation=UsageSourceState.objects.filter(key='local').values_list('generation',flat=True).first()
    sample_query=session.samples.all()
    if generation is not None:
        sample_query=sample_query.filter(source_generation=generation)
    rows=list(sample_query)
    jobs=jobs.get(session.external_id,[])
    if not jobs:return rows,None
    fields={'input':'input_tokens','cached':'cached_input_tokens','output':'output_tokens','reasoning':'reasoning_output_tokens'}
    values={field:0 for field in fields}
    for job in jobs:
        usage=job.token_usage
        if not isinstance(usage,dict) or any(type(usage.get(key)) is not int or usage[key]<0 for key in fields.values()):
            return rows,None
        current={field:usage[key] for field,key in fields.items()}
        if current['cached']>current['input'] or current['reasoning']>current['output']:return rows,None
        total=current['input']+current['output']
        if 'total_tokens' in usage and (type(usage['total_tokens']) is not int or usage['total_tokens']!=total):return rows,None
        for key in values:values[key]+=current[key]
    total=values['input']+values['output']
    models={job.observed_model for job in jobs}
    efforts={job.requested_thinking for job in jobs}
    job=DirectUsageGroup(jobs=jobs,token_usage={
        'input_tokens':values['input'],'cached_input_tokens':values['cached'],
        'output_tokens':values['output'],'reasoning_output_tokens':values['reasoning'],
        'total_tokens':total},finished_at=max(item.finished_at for item in jobs),
        observed_model=next(iter(models)) if len(models)==1 else '',
        requested_thinking=next(iter(efforts)) if len(efforts)==1 else '')
    # A later external turn may extend the execution session. Never add its
    # cumulative counter to the already recorded CLI turn or turns.
    point=TokenSample(session=session,source_id=0,sampled_at=job.finished_at,
        model=job.observed_model,effort=job.requested_thinking,total=total,
        context=0,context_limit=0,**values)
    # Retain the known CLI anchor and only subsequent growth, never add
    # a second cumulative copy of the completed execution.
    return [point]+[row for row in rows if row.total>total],job


def dashboard_samples(project, session_id=None):
    selected = TrackedSession.objects.filter(project=project)
    if session_id:
        selected = selected.filter(pk=session_id)
    jobs = job_index(project)
    from .models import UsageSourceState
    generation=UsageSourceState.objects.filter(key='local').values_list('generation',flat=True).first()
    return [(session, *effective_samples(session, jobs, generation))
            for session in selected.prefetch_related('samples')]
