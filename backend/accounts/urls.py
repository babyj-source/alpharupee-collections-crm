from django.urls import path

from . import users, views

urlpatterns = [
    path("csrf", views.CsrfView.as_view()),
    path("login", views.LoginView.as_view()),
    path("verify", views.VerifyView.as_view()),
    path("resend", views.ResendView.as_view()),
    path("forgot", views.ForgotView.as_view()),
    path("reset", views.ResetView.as_view()),
    path("logout", views.LogoutView.as_view()),
    path("me", views.MeView.as_view()),
    path("password", users.ChangePasswordView.as_view()),
]
