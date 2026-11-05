from django.urls import path

from . import users

urlpatterns = [
    path("users", users.UserListView.as_view()),
    path("users/<int:pk>", users.UserDetailView.as_view()),
    path("users/<int:pk>/reset-password", users.UserResetPasswordView.as_view()),
    path("users/<int:pk>/unlock", users.UserUnlockView.as_view()),
    path("roles", users.RolesView.as_view()),
]
