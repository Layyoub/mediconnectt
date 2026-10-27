from django.urls import path

from . import views

app_name = "appointments"

urlpatterns = [
    # Patient
    path("rendez-vous/", views.my_appointments, name="list"),
    path("rendez-vous/nouveau/", views.book, name="book"),
    path("rendez-vous/medecin/<int:pk>/", views.doctor_slots, name="doctor_slots"),
    # Shared
    path("rendez-vous/<int:pk>/", views.detail, name="detail"),
    path("rendez-vous/<int:pk>/annuler/", views.cancel, name="cancel"),
    # Doctor
    path("rendez-vous/<int:pk>/decision/", views.decide, name="decide"),
    path("espace-medecin/demandes/", views.requests, name="requests"),
    path("espace-medecin/agenda/", views.agenda, name="agenda"),
    path("espace-medecin/disponibilites/", views.availability, name="availability"),
    path("espace-medecin/disponibilites/<int:pk>/supprimer/", views.delete_availability, name="delete_availability"),
]
