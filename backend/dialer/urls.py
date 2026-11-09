from django.urls import path

from . import calllog, views

urlpatterns = [
    path("status", views.StatusView.as_view()),
    path("pools", views.PoolListView.as_view()),
    path("campaigns", views.CampaignCreateView.as_view()),
    path("live", views.LiveView.as_view()),
    path("setup", views.SetupView.as_view()),
    path("smartflo/users", views.SmartfloUsersView.as_view()),
    path("smartflo/outcomes", views.SmartfloOutcomesView.as_view()),
    path("smartflo/rules/<str:key>", views.SmartfloRulesView.as_view()),
    path("smartflo/campaign/<str:key>", views.SmartfloCampaignView.as_view()),
    path("campaigns/<str:key>", views.CampaignUpdateView.as_view()),
    path("dispositions", views.DispositionMapView.as_view()),
    path("agents/<int:user_id>", views.AgentProfileView.as_view()),
    path("calls", calllog.CallLogView.as_view()),
    path("calls/recording", calllog.RecordingView.as_view()),
    path("check", views.CheckView.as_view()),
    path("session", views.SessionView.as_view()),
    path("session/start", views.SessionStartView.as_view()),
    path("session/push", views.SessionPushView.as_view()),
    path("session/stop", views.SessionStopView.as_view()),
    path("session/dismiss", views.SessionDismissView.as_view()),
    path("session/release", views.SessionReleaseView.as_view()),
    path("session/leads", views.SessionLeadsView.as_view()),
    path("session/resend", views.SessionResendView.as_view()),
    path("session/outcome/<int:pk>", views.SessionOutcomeView.as_view()),
    path("call", views.CallView.as_view()),
    path("call/status", views.CallStatusView.as_view()),
    path("call/hangup", views.CallHangupView.as_view()),
]
