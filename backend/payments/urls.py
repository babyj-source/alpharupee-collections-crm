from django.urls import path

from . import views

urlpatterns = [
    path("links", views.LinksListView.as_view()),
    path("summary", views.LinksSummaryView.as_view()),
    path("link", views.LinkView.as_view()),
    path("link/<int:pk>/refresh", views.LinkRefreshView.as_view()),
    path("webhook", views.EasebuzzWebhookView.as_view()),
    path("webhook-log", views.WebhookLogView.as_view()),
]
