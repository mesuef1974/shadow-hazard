from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from registry import portal
urlpatterns = [
 path('', portal.home, name='project-home'),
 path('login/', auth_views.LoginView.as_view(template_name='registry/login.html'), name='login'),
 path('documents/<path:name>', portal.document, name='project-document'),
 path('admin/', admin.site.urls),
]
