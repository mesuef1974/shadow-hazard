from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from registry import portal
from workspace import views as workspace
from token_usage import views as token_usage
from execution_context import views as execution_context
urlpatterns = [
 path('sessions/escalations/',execution_context.escalation_list,name='escalations'),
 path('sessions/escalations/<int:pk>/decision/',execution_context.decide_escalation,name='decide-escalation'),
 path('sessions/jobs/<int:job_id>/escalation/',execution_context.request_escalation,name='request-escalation'),
 path('sessions/automation/', workspace.automation, name='automation'),
 path('sessions/jobs/', workspace.model_jobs, name='model-jobs'),
 path('sessions/', workspace.sessions, name='project-sessions'),
 path('usage/', token_usage.dashboard, name='token-dashboard'),
 path('usage/sync/', token_usage.sync, name='token-sync'),
 path('', workspace.dashboard, name='project-home'),
 path('library/', portal.home, name='project-library'),
 path('phases/', workspace.phases, name='project-phases'),
 path('tasks/', workspace.tasks, name='project-tasks'),
 path('tasks/new/', workspace.task_create, name='task-create'),
 path('tasks/<int:pk>/', workspace.task_detail, name='task-detail'),
 path('decisions/', workspace.decisions, name='project-decisions'),
 path('exhibition/', workspace.exhibition, name='project-exhibition'),
 path('logout/', auth_views.LogoutView.as_view(next_page='/login/'), name='logout'),
 path('login/', auth_views.LoginView.as_view(template_name='registry/login.html'), name='login'),
 path('documents/<path:name>', portal.document, name='project-document'),
 path('admin/', admin.site.urls),
]
