from django.urls import path

from . import training_views

urlpatterns = [
    path("", training_views.index, name="training-index"),
    path("project/", training_views.project, name="training-project"),
    path("lessons/<int:pk>/", training_views.lesson, name="training-lesson"),
    path("recordings/<int:pk>/", training_views.recording, name="training-recording"),
    path("slides/<int:pk>/image", training_views.slide_image, name="training-slide-image"),
]
