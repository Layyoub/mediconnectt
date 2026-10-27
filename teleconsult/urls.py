from django.urls import path

from . import views

app_name = "teleconsult"

urlpatterns = [
    path("consultations/", views.consultation_list, name="list"),
    path("consultations/<str:code>/", views.room, name="room"),
    path("consultations/<str:code>/messages/", views.chat, name="chat"),
    path("consultations/<str:code>/signal/", views.signal, name="signal"),
    path("consultations/<str:code>/documents/", views.share_document, name="share_document"),
    path("consultations/<str:code>/terminer/", views.finish, name="finish"),
    path("consultations/<str:code>/compte-rendu/", views.summary, name="summary"),
    path("notifications/", views.notification_list, name="notifications"),
    path("notifications/<int:pk>/", views.notification_open, name="notification_open"),
    path("notifications/tout-lire/", views.notification_read_all, name="notifications_read_all"),
]
