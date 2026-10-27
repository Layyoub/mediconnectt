from django.urls import include, path

from . import account, backoffice, views

backoffice_patterns = (
    [
        path("", backoffice.overview, name="overview"),
        path("medecins/", backoffice.doctor_list, name="doctor_list"),
        path("medecins/<int:pk>/", backoffice.doctor_detail, name="doctor_detail"),
        path("medecins/<int:pk>/decision/", backoffice.doctor_decision, name="doctor_decision"),
        path("medecins/<int:pk>/analyse/", backoffice.doctor_verify, name="doctor_verify"),
        path("medecins/<int:pk>/documents/<str:kind>/", backoffice.doctor_document, name="doctor_document"),
        path("patients/", backoffice.patient_list, name="patient_list"),
        path("triage/", backoffice.triage_overview, name="triage"),
        path("triage/regles/<int:pk>/basculer/", backoffice.toggle_rule, name="toggle_rule"),
        path("rendez-vous/", backoffice.appointments_overview, name="appointments"),
    ],
    "backoffice",
)

urlpatterns = [
    # Public
    path("", views.home, name="home"),
    path("mentions-legales/", views.legal, name="legal"),
    path("bientot/", views.coming_soon, name="coming_soon"),

    # Authentication
    path("connexion/", views.LoginView.as_view(), name="login"),
    path("deconnexion/", views.LogoutView.as_view(), name="logout"),
    path("inscription/", views.signup_choice, name="signup"),
    path("inscription/patient/", views.signup_patient, name="signup_patient"),
    path("inscription/medecin/", views.signup_doctor, name="signup_doctor"),
    path("mot-de-passe-oublie/", views.PasswordResetView.as_view(), name="password_reset"),
    path("mot-de-passe-oublie/envoye/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "reinitialiser/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("reinitialiser/termine/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),

    # User spaces
    path("tableau-de-bord/", views.dashboard, name="dashboard"),
    path("espace-patient/", views.patient_dashboard, name="patient_dashboard"),
    path("espace-medecin/", views.doctor_dashboard, name="doctor_dashboard"),
    path("mon-compte/profil/", account.profile, name="account_profile"),
    path("mon-compte/parametres/", account.settings_view, name="account_settings"),

    # Staff back-office
    path("gestion/", include(backoffice_patterns)),
]
