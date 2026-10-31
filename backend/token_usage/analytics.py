"""Conservative interval attribution: boundaries and initial counters stay unassigned."""
from collections import defaultdict
from datetime import timedelta
from django.utils import timezone
from .models import TrackedSession


def analyze(project, period='all'):
    cutoff = timezone.now()-timedelta(days=1 if period=='day' else 7) if period in {'day','week'} else None
    models=defaultdict(lambda:{'tokens':0,'intervals':0})
    hours=defaultdict(int)
    split={'input':0,'cached':0,'output':0,'reasoning':0}
    transitions=[]; baseline=0; boundary=0; resets=0; intervals=0; measured=0; anomalies=0
    context=[]
    for session in TrackedSession.objects.filter(project=project).prefetch_related('samples'):
        rows=list(session.samples.all())
        if not rows:continue
        if not cutoff:baseline+=rows[0].total
        high_total=rows[0].total
        high_split={key:getattr(rows[0],key) for key in split}
        for row in rows:
            if cutoff and row.sampled_at<cutoff:continue
            context.append({'at':timezone.localtime(row.sampled_at).strftime('%m/%d %H:%M'),'percent':round(row.context/row.context_limit*100,1) if row.context_limit else 0,'session':session.name})
        for previous,current in zip(rows,rows[1:]):
            delta=max(0,current.total-high_total)
            diffs={key:max(0,getattr(current,key)-high_split[key]) for key in split}
            recovering=previous.total<high_total
            high_total=max(high_total,current.total)
            for key in split:high_split[key]=max(high_split[key],getattr(current,key))
            if cutoff and previous.sampled_at<cutoff:continue
            if current.model!=previous.model:
                transitions.append({'at':current.sampled_at,'before':previous.model,'after':current.model,'session':session.name})
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
    maximum=max(hours.values(),default=1)
    hourly=[{'at':hour,'tokens':value,'height':round(value/maximum*100,2)} for hour,value in sorted(hours.items())]
    composition=[{'name':'إدخال غير مخزن','value':split['input']-split['cached'],'class':'uncached'}, {'name':'إدخال مخزن مؤقتًا','value':split['cached'],'class':'cached'}, {'name':'إخراج غير استدلالي','value':split['output']-split['reasoning'],'class':'output'}, {'name':'استدلال ضمن الإخراج','value':split['reasoning'],'class':'reasoning'}]
    denominator=sum(x['value'] for x in composition)
    for item in composition:item['share']=round(item['value']/denominator*100,2) if denominator else 0
    return {'period':period,'models':model_rows,'hourly':hourly,'composition':composition,'measured':measured,'baseline':baseline,'boundary':boundary,'resets':resets,'anomalies':anomalies,'intervals':intervals,'transitions':sorted(transitions,key=lambda x:x['at'],reverse=True)[:20], 'cache_rate':round(split['cached']/split['input']*100,1) if split['input'] else None,'peak':max(hourly,key=lambda x:x['tokens'],default=None),'context':context[-80:]}
