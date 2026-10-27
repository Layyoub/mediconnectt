from django.urls import path

from . import views

app_name = "pharmacheck"

urlpatterns = [
    path("verifier/", views.verify, name="verify"),
]
