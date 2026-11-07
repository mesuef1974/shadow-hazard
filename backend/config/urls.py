from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from registry import portal
from workspace import views as workspace
from automation_log import views as automation_log
from token_usage import views as token_usage
from claude_usage import views as claude_usage
from execution_context import views as execution_context
from releases.pages import dashboard as release_dashboard
from knowledge.views import (search_view as knowledge_search, search_page as knowledge_page,
    answer_page as knowledge_answer, documentation_detail as knowledge_documentation_detail)
urlpatterns = [
 path('simulation/', include('simulation.urls')),
 path('knowledge/', knowledge_page, name='knowledge-page'),
 path('knowledge/answer/', knowledge_answer, name='knowledge-answer'),
 path('knowledge/search/', knowledge_search, name='knowledge-search'),
 path('knowledge/documentation/<int:documentation_id>/', knowledge_documentation_detail, name='knowledge-documentation-detail'),
 path('releases/', include('releases.urls')),
 path('sessions/releases/', release_dashboard, name='release-dashboard'),
 path('sessions/escalations/',execution_context.escalation_list,name='escalations'),
 path('sessions/escalations/<int:pk>/decision/',execution_context.decide_escalation,name='decide-escalation'),
 path('sessions/jobs/<int:job_id>/escalation/',execution_context.request_escalation,name='request-escalation'),
 path('sessions/automation/flow/', automation_log.flow, name='automation-flow'),
 path('sessions/automation/flow/data.json', automation_log.data, name='automation-flow-data'),
 path('sessions/automation/flow/export.csv', automation_log.export, name='automation-flow-export'),
 path('sessions/automation/', workspace.automation, name='automation'),
 path('sessions/automation-model/', workspace.automation_model, name='session-automation-model'),
 path('sessions/automation-model/live.json', workspace.automation_live, name='session-automation-live'),
 path('sessions/automation-model/assets/<str:asset>', workspace.automation_model, name='session-automation-asset'),
 path('sessions/jobs/', workspace.model_jobs, name='model-jobs'),
 path('sessions/', workspace.sessions, name='project-sessions'),
 path('usage/', token_usage.dashboard, name='token-dashboard'),
 path('claude-usage/', claude_usage.dashboard, name='claude-usage'),
 path('claude-usage/quota/', claude_usage.record_quota, name='claude-usage-quota'),
 path('usage/sync/', token_usage.sync, name='token-sync'),
 path('', workspace.dashboard, name='project-home'),
 path('library/', portal.home, name='project-library'),
 path('phases/', workspace.phases, name='project-phases'),
 path('tasks/', workspace.tasks, name='project-tasks'),
 path('tasks/new/', workspace.task_create, name='task-create'),
 path('tasks/<int:pk>/', workspace.task_detail, name='task-detail'),
 path('decisions/', workspace.decisions, name='project-decisions'),
 path('exhibition/', workspace.exhibition, name='project-exhibition'),
 path('exhibition/archive/', workspace.exhibition_archive, name='project-exhibition-archive'),
 path('archive/', workspace.prior_design_archive, name='project-archive'),
 path('logout/', auth_views.LogoutView.as_view(next_page='/login/'), name='logout'),
 path('login/', auth_views.LoginView.as_view(template_name='registry/login.html'), name='login'),
 path('documents/<path:name>', portal.document, name='project-document'),
 path('admin/', admin.site.urls),
]
