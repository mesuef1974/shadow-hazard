from django.urls import path
from . import views

urlpatterns = [path('', views.index, name='simulation-lab'),
               path('preview/', views.execute, {'persist': False}, name='simulation-preview'),
               path('run/', views.execute, name='simulation-run'),
               path('runs/<int:pk>/', views.detail, name='simulation-detail')]
