from django.urls import path

from . import views

urlpatterns = [
    path("link", views.LinkView.as_view()),
    path("link/<int:pk>/refresh", views.LinkRefreshView.as_view()),
    path("webhook", views.EasebuzzWebhookView.as_view()),
    path("webhook-log", views.WebhookLogView.as_view()),
]
