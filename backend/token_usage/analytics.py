"""Conservative interval attribution: boundaries and initial counters stay unassigned."""
from collections import defaultdict
from datetime import datetime, timedelta
from django.utils import timezone
from .models import TrackedSession
from .delegated import job_index, effective_samples


def analyze(project, period='all', session_id=None):
    cutoff = timezone.now()-timedelta(days=1 if period=='day' else 7) if period in {'day','week'} else None
    models=defaultdict(lambda:{'tokens':0,'intervals':0})
    hours=defaultdict(int)
    split={'input':0,'cached':0,'output':0,'reasoning':0}
    transitions=[]; baseline=0; boundary=0; resets=0; intervals=0; measured=0; anomalies=0
    context=[]
    selected=TrackedSession.objects.filter(project=project)
    if session_id:selected=selected.filter(pk=session_id)
    jobs=job_index(project)
    for session in selected.prefetch_related('samples'):
        rows,job=effective_samples(session,jobs)
        if not rows:continue
        if job:
            # One independent CLI execution has an explicit per-turn counter.
            # Unlike an arbitrary collector baseline, its entire usage is known.
            row=rows[0]
            if not cutoff or row.sampled_at>=cutoff:
                measured+=row.total;intervals+=1
                hours[timezone.localtime(row.sampled_at).strftime('%Y-%m-%d %H:00')]+=row.total
                if row.model:
                    models[row.model]['tokens']+=row.total
                    models[row.model]['intervals']+=1
                else:boundary+=row.total
                for key in split:split[key]+=getattr(row,key)
        if not cutoff and not job:baseline+=rows[0].total
        high_total=rows[0].total
        high_split={key:getattr(rows[0],key) for key in split}
        for row in rows:
            if job and row is rows[0]:continue
            if cutoff and row.sampled_at<cutoff:continue
            context.append({'timestamp':row.sampled_at.isoformat(),'at':timezone.localtime(row.sampled_at).strftime('%m/%d %H:%M'),'percent':round(row.context/row.context_limit*100,1) if row.context_limit else None,'tokens':row.context,'limit':row.context_limit,'session':session.name,'session_id':session.pk})
        for previous,current in zip(rows,rows[1:]):
            delta=max(0,current.total-high_total)
            diffs={key:max(0,getattr(current,key)-high_split[key]) for key in split}
            recovering=previous.total<high_total
            high_total=max(high_total,current.total)
            for key in split:high_split[key]=max(high_split[key],getattr(current,key))
            if cutoff and previous.sampled_at<cutoff:continue
            if current.model!=previous.model:
                transitions.append({'at':current.sampled_at,'before':previous.model,'after':current.model,'session':session.name,'session_id':session.pk})
            if current.total<previous.total:resets+=1;continue
            if not delta:continue
            measured+=delta;intervals+=1
            hour=timezone.localtime(current.sampled_at).strftime('%Y-%m-%d %H:00')
            hours[hour]+=delta
            if recovering or not current.model or current.model!=previous.model:boundary+=delta
            else:
                models[current.model]['tokens']+=delta
                models[current.model]['intervals']+=1
            if recovering or diffs['input']+diffs['output']!=delta or diffs['cached']>diffs['input'] or diffs['reasoning']>diffs['output']:
                anomalies+=1
            else:
                for key in split:split[key]+=diffs[key]
    model_rows=[{'name':name,**values,'share':round(values['tokens']/measured*100,1) if measured else 0} for name,values in sorted(models.items(),key=lambda item:item[1]['tokens'],reverse=True)]
    if hours:
        if cutoff:
            first_hour=timezone.localtime(cutoff).replace(minute=0,second=0,microsecond=0)
            last_hour=timezone.localtime(timezone.now()).replace(minute=0,second=0,microsecond=0)
        else:
            first_hour=datetime.strptime(min(hours), '%Y-%m-%d %H:00').replace(tzinfo=timezone.get_current_timezone())
            last_hour=datetime.strptime(max(hours), '%Y-%m-%d %H:00').replace(tzinfo=timezone.get_current_timezone())
        hourly_values=[]
        current_hour=first_hour
        while current_hour<=last_hour:
            key=current_hour.strftime('%Y-%m-%d %H:00')
            hourly_values.append((key,hours.get(key,0)))
            current_hour+=timedelta(hours=1)
    else:
        hourly_values=[]
    maximum=max((value for _,value in hourly_values),default=1) or 1
    # Keep the last-day chart readable while exposing the hourly rhythm clearly.
    label_step=3 if len(hourly_values)<=30 else 12
    hourly=[]
    for index,(hour,value) in enumerate(hourly_values):
        day_start=index==0 or hour[:10]!=hourly_values[index-1][0][:10]
        label=f'{hour[5:10]} {hour[11:13]}' if day_start and len(hourly_values)>30 else hour[11:13] if index%label_step==0 else ''
        hourly.append({'at':hour,'tokens':value,'height':round(value/maximum*100,2),
                       'label':label,'day':hour[:10],'day_start':day_start})
    hourly_mid=maximum/2
    composition=[{'name':'إدخال غير مخزن','value':split['input']-split['cached'],'class':'uncached'}, {'name':'إدخال مخزن مؤقتًا','value':split['cached'],'class':'cached'}, {'name':'إخراج غير استدلالي','value':split['output']-split['reasoning'],'class':'output'}, {'name':'استدلال ضمن الإخراج','value':split['reasoning'],'class':'reasoning'}]
    denominator=sum(x['value'] for x in composition)
    for item in composition:item['share']=round(item['value']/denominator*100,2) if denominator else 0
    peak=max((row for row in hourly if row['tokens']>0),key=lambda x:x['tokens'],default=None)
    return {'period':period,'models':model_rows,'hourly':hourly,'hourly_max':maximum,'hourly_mid':hourly_mid,'composition':composition,'measured':measured,'baseline':baseline,'boundary':boundary,'resets':resets,'anomalies':anomalies,'intervals':intervals,'transitions':sorted(transitions,key=lambda x:x['at'],reverse=True)[:20], 'cache_rate':round(split['cached']/split['input']*100,1) if split['input'] else None,'peak':peak,'context':sorted(context,key=lambda x:x['timestamp'])}

