from django.urls import path
from . import views

app_name = "followups"
urlpatterns = [
    path("suivi/", views.patient_plans, name="patient_plans"),
    path("espace-medecin/suivi/", views.doctor_plans, name="doctor_plans"),
    path("espace-medecin/patients/<int:patient_id>/suivi/creer/<int:appointment_id>/", views.create_plan, name="create_plan"),
    path("suivi/<int:pk>/", views.plan_detail, name="detail"),
    path("suivi/<int:pk>/modifier/", views.edit_plan, name="edit_plan"),
    path("suivi/<int:pk>/supprimer/", views.delete_plan, name="delete_plan"),
    path("suivi/<int:pk>/tache/ajouter/", views.add_task, name="add_task"),
    path("suivi/<int:pk>/tache/<int:task_id>/modifier/", views.edit_task, name="edit_task"),
    path("suivi/<int:pk>/tache/<int:task_id>/supprimer/", views.delete_task, name="delete_task"),
    path("suivi/<int:pk>/tache/<int:task_id>/avancement/", views.update_progress, name="update_progress"),
    path("suivi/<int:pk>/suggestions-ia/", views.ai_suggestions, name="ai_suggestions"),
    path("suivi/<int:pk>/suggestions-ia/ajouter/", views.add_suggestion, name="add_suggestion"),
]
