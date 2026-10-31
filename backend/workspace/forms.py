from django import forms
from .models import Task, TaskUpdate, Decision, SessionCharter
class TaskForm(forms.ModelForm):
    class Meta:
        model=Task
        fields=['phase','title','description','assignee','due_date']
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
    charter=forms.ModelChoiceField(label='الجلسة ونوع المهمة',queryset=SessionCharter.objects.none())
    prompt=forms.CharField(label='المهمة المطلوبة ومعيار الإنجاز',max_length=12000,widget=forms.Textarea)
    mode=forms.ChoiceField(label='صلاحية التنفيذ',choices=[('read-only','قراءة ومراجعة فقط'),('workspace-write','السماح بتعديل ملفات المشروع')])
