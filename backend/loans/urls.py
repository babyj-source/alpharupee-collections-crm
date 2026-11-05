from django.urls import path

from . import bulk, views

urlpatterns = [
    path("meta", views.MetaView.as_view()),
    path("dashboard", views.DashboardView.as_view()),
    path("loans", views.LoanListView.as_view()),
    path("loans/assign", views.AssignView.as_view()),
    path("loans/export.csv", views.ExportView.as_view()),
    path("loans/<int:pk>", views.LoanDetailView.as_view()),
    path("loans/<int:pk>/mobile", views.RevealMobileView.as_view()),
    path("loans/<int:pk>/disposition", views.DispositionView.as_view()),
    path("loans/<int:pk>/timeline", views.TimelineView.as_view()),
    path("loans/<int:pk>/references/<int:slot>", views.ReferenceView.as_view()),
    path("loans/<int:pk>/callback", views.CallbackClearView.as_view()),
    path("callbacks", views.CallbackListView.as_view()),
    path("ptps", views.PtpListView.as_view()),
    path("ptps/<int:pk>", views.PtpDetailView.as_view()),
    path("allocation/csv", bulk.AllocationCsvView.as_view()),
    path("allocation/history", bulk.AllocationHistoryView.as_view()),
    path("allocation/template.csv", bulk.AllocationTemplateView.as_view()),
    path("references/csv", bulk.ReferenceCsvView.as_view()),
    path("agents", views.AgentListView.as_view()),
]
