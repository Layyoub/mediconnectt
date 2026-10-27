from django.urls import path

from . import views

app_name = "triage"

urlpatterns = [
    path("", views.home, name="home"),
    path("nouvelle/", views.start, name="start"),
    path("<int:pk>/contexte/", views.edit_context, name="context"),
    path("<int:pk>/symptomes/", views.symptoms, name="symptoms"),
    path("<int:pk>/verification/", views.review, name="review"),
    path("<int:pk>/resultat/", views.result, name="result"),
    path("<int:pk>/abandonner/", views.cancel, name="cancel"),
]
