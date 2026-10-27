from django.urls import path

from . import views

app_name = "records"

urlpatterns = [
    # Patient
    path("dossier/", views.my_record, name="my_record"),
    path("dossier/modifier/", views.edit_my_record, name="edit_my_record"),
    path("dossier/documents/ajouter/", views.add_my_document, name="add_my_document"),

    # Doctor
    path("espace-medecin/patients/", views.patient_list, name="patients"),
    path("espace-medecin/patients/<int:patient_id>/", views.patient_record, name="patient_record"),
    path("espace-medecin/patients/<int:patient_id>/modifier/", views.doctor_edit_record, name="doctor_edit_record"),
    path("espace-medecin/patients/<int:patient_id>/notes/ajouter/", views.add_note, name="add_note"),
    path("espace-medecin/patients/<int:patient_id>/documents/ajouter/", views.doctor_add_document, name="doctor_add_document"),
    path("espace-medecin/patients/<int:patient_id>/ordonnance/", views.new_prescription, name="new_prescription"),

    # Shared
    path("ordonnances/", views.prescription_list, name="prescriptions"),
    path("ordonnances/<str:code>/", views.prescription_detail, name="prescription"),
    path("ordonnances/<str:code>/consignes/", views.update_patient_instructions, name="patient_instructions"),
    path("documents/<int:pk>/", views.document_file, name="document"),
    path("documents/<int:pk>/supprimer/", views.document_delete, name="document_delete"),
    path("documents/<int:pk>/analyse-ia/", views.document_analysis, name="document_analysis"),
    path("documents/<int:pk>/analyse-ia/donnees/", views.document_analysis_api, name="document_analysis_api"),
]
