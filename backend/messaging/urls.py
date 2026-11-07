from django.urls import path

from . import views

urlpatterns = [
    path("whatsapp/preview", views.WhatsappPreviewView.as_view()),
    path("whatsapp/send", views.WhatsappSendView.as_view()),
    path("whatsapp/chat", views.ChatView.as_view()),
    path("whatsapp/call", views.WhatsappCallView.as_view()),
    path("whatsapp/inbox", views.InboxView.as_view()),
    path("whatsapp/webhook", views.WhatsappWebhookView.as_view()),
    path("whatsapp/webhook-log", views.WhatsappWebhookLogView.as_view()),
    path("sent", views.SentLogView.as_view()),
    path("sms", views.SendSmsView.as_view()),
    path("log", views.MessageLogView.as_view()),
    path("preview", views.PreviewView.as_view()),
    path("templates", views.TemplateListView.as_view()),
    path("templates/<str:key>", views.TemplateUpdateView.as_view()),
    path("whatsapp", views.WhatsappListView.as_view()),
    path("whatsapp/<str:key>", views.WhatsappUpdateView.as_view()),
]
