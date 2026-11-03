from django.urls import path
from . import views

urlpatterns = [
    path('', views.collection, name='release-list'),
    path('<int:pk>/', views.detail, name='release-detail'),
    path('<int:pk>/actions/', views.action, name='release-action'),
    path('<int:pk>/deployments/', views.deployment, name='release-deploy'),
    path('<int:pk>/merge/', views.merge, name='release-merge'),
]
