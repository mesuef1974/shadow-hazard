from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from registry import portal
from workspace import views as workspace
urlpatterns = [
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
