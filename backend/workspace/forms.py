from django import forms
from .models import Task, TaskUpdate, Decision, SessionCharter
class TaskForm(forms.ModelForm):
    class Meta:
        model=Task
        fields=['phase','title','description','assignee','due_date','owner_only']
        widgets={'due_date':forms.DateInput(attrs={'type':'date'})}
class UpdateForm(forms.ModelForm):
    class Meta:
        model=TaskUpdate
        fields=['body','state','hours']
    def __init__(self,*args,supervisor=False,**kwargs):
        super().__init__(*args,**kwargs)
        if not supervisor:self.fields['state'].choices=Task.STATES[:-1]
        self.fields['hours'].min_value=0
    def clean_hours(self):
        value=self.cleaned_data.get('hours')
        if value is not None and value<0:raise forms.ValidationError('الساعات لا تكون سالبة.')
        return value
class DecisionForm(forms.ModelForm):
    class Meta:
        model=Decision
        fields=['title','body','private']

class ModelJobForm(forms.Form):
    submission_key=forms.UUIDField(widget=forms.HiddenInput)
    charter=forms.ModelChoiceField(label='الجلسة المستهدفة',queryset=SessionCharter.objects.none(),
        help_text='يحدد اختيارك الجلسة التي ستستلم الطلب؛ يحدد نوع المهمة سياسة النموذج والتفكير فقط.')
    prompt=forms.CharField(label='المهمة المطلوبة ومعيار الإنجاز',max_length=12000,widget=forms.Textarea)
    mode=forms.ChoiceField(label='صلاحية التنفيذ',choices=[('read-only','قراءة فقط — عامل معزول بالحاوية'),('workspace-write','السماح بتعديل ملفات المشروع')])
    from execution_context.routing import KINDS, SKILLS
    task_kind=forms.ChoiceField(label='نوع المهمة',choices=KINDS,initial='auto',help_text='التلقائي ينفذ تعبيرًا مثل 17 + 25 أو احسب 17 + 25 محليًا بصفر توكن؛ ويصنف بقية الطلبات للبرمجة أو التصميم أو النص والمراجعة.')
    skills=forms.MultipleChoiceField(label='مهارات إضافية عند الحاجة فقط',choices=[(key,value[1]) for key,value in SKILLS.items()],required=False,widget=forms.CheckboxSelectMultiple)
    plugins=forms.MultipleChoiceField(label='موصلات محددة — لن تُحمّل بقية الموصلات',choices=[],required=False,widget=forms.CheckboxSelectMultiple)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from execution_context.routing import available_plugins
        self.fields['plugins'].choices=[(name,name) for name in available_plugins()]
        self.fields['plugins'].help_text='اختر موصلين كحد أقصى، وحدد نوع «مهمة تحتاج موصلًا محددًا». اتركها فارغة إذا لم تحتج موصلًا.'
        self.fields['skills'].help_text='اختر ثلاث مهارات إضافية كحد أقصى، عند الحاجة فقط.'

    def clean_plugins(self):
        from execution_context.routing import MAX_PLUGINS
        values=self.cleaned_data['plugins']
        if len(values)>MAX_PLUGINS:
            raise forms.ValidationError('اختر موصلين كحد أقصى؛ أزل الموصلات الزائدة ثم أعد الحفظ.')
        return values

    def clean_skills(self):
        from execution_context.routing import MAX_SKILLS
        values=self.cleaned_data['skills']
        if len(values)>MAX_SKILLS:
            raise forms.ValidationError('اختر ثلاث مهارات إضافية كحد أقصى.')
        return values

    def clean(self):
        data=super().clean()
        if 'plugins' not in data or 'task_kind' not in data:
            return data
        if data['plugins'] and data['task_kind']!='connected':
            self.add_error('plugins','لاستخدام الموصلات اختر نوع «مهمة تحتاج موصلًا محددًا»، أو ألغِ اختيار الموصلات.')
        elif data['task_kind']=='connected' and not data['plugins']:
            self.add_error('plugins','حدد موصلًا واحدًا أو موصلين لهذه المهمة.')
        return data
