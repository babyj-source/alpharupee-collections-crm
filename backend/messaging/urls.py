from django.urls import path

from . import views

urlpatterns = [
    path("sms", views.SendSmsView.as_view()),
    path("log", views.MessageLogView.as_view()),
    path("templates", views.TemplateListView.as_view()),
    path("templates/<str:key>", views.TemplateUpdateView.as_view()),
    path("whatsapp", views.WhatsappListView.as_view()),
    path("whatsapp/<str:key>", views.WhatsappUpdateView.as_view()),
]
