from django import forms
from .models import Task, TaskUpdate, Decision
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
