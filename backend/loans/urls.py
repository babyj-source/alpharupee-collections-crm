from django.urls import path

from . import views

urlpatterns = [
    path("dashboard", views.DashboardView.as_view()),
    path("loans", views.LoanListView.as_view()),
    path("loans/assign", views.AssignView.as_view()),
    path("loans/<int:pk>", views.LoanDetailView.as_view()),
    path("loans/<int:pk>/mobile", views.RevealMobileView.as_view()),
    path("agents", views.AgentListView.as_view()),
]
