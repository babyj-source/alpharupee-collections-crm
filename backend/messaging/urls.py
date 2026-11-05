from django.urls import path

from . import views

urlpatterns = [
    path("templates", views.TemplateListView.as_view()),
    path("templates/<str:key>", views.TemplateUpdateView.as_view()),
]
