"""A completed CLI job is a direct source, not an extra charge beside the collector."""
from .models import TokenSample
from workspace.models import ModelJob


def job_index(project):
    return {job.execution_thread_id:job for job in ModelJob.objects.filter(
        charter__project=project,execution_thread_id__gt='',finished_at__isnull=False
    ).exclude(token_usage={}).only('pk','execution_thread_id','finished_at','token_usage','observed_model','requested_thinking').order_by('pk')}


def effective_samples(session, jobs):
    rows=list(session.samples.all())
    job=jobs.get(session.external_id)
    if not job:return rows,None
    usage=job.token_usage
    fields={'input':'input_tokens','cached':'cached_input_tokens','output':'output_tokens','reasoning':'reasoning_output_tokens'}
    if not isinstance(usage,dict) or any(type(usage.get(key)) is not int or usage[key]<0 for key in fields.values()):
        return rows,None
    values={field:usage[key] for field,key in fields.items()}
    if values['cached']>values['input'] or values['reasoning']>values['output']:return rows,None
    total=values['input']+values['output']
    if 'total_tokens' in usage and usage['total_tokens']!=total:return rows,None
    # A later external turn may extend the execution session. Never add its
    # cumulative counter to the already recorded CLI turn.
    point=TokenSample(session=session,source_id=0,sampled_at=job.finished_at,
        model=job.observed_model,effort=job.requested_thinking,total=total,
        context=0,context_limit=0,**values)
    # Retain the known CLI anchor and only subsequent growth, never add
    # a second cumulative copy of the completed execution.
    return [point]+[row for row in rows if row.total>total],job
