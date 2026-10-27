from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from .forms import (
    DoctorSignupForm,
    EmailAuthenticationForm,
    PatientSignupForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from appointments.models import Appointment
from records.models import Prescription
from teleconsult.models import ONLINE_WINDOW, ConsultationRoom, ConsultationSummary
from triage.models import TriageResult

from .models import get_role


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def role_required(role):
    """Restrict a view to users with the given role."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if get_role(request.user) != role:
                return redirect("dashboard")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _redirect_if_authenticated(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return view(request, *args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------

def home(request):
    return render(request, "core/public/home.html")


def legal(request):
    return render(request, "core/public/legal.html")


FEATURES = {
    "triage": "Évaluation des symptômes",
    "appointments": "Rendez-vous",
    "consultations": "Téléconsultation",
    "prescriptions": "Ordonnances",
    "reports": "Comptes rendus",
    "requests": "Demandes de consultation",
    "agenda": "Agenda",
    "triage_records": "Dossiers de triage",
    "profile": "Profil",
    "settings": "Paramètres",
}


def coming_soon(request):
    feature = FEATURES.get(request.GET.get("f", ""), "")
    return render(request, "core/public/coming_soon.html", {"feature": feature})


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

class LoginView(auth_views.LoginView):
    template_name = "core/auth/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    next_page = "home"


@_redirect_if_authenticated
def signup_choice(request):
    return render(request, "core/auth/signup_choice.html")


@_redirect_if_authenticated
def signup_patient(request):
    form = PatientSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Bienvenue sur MediConnect ! Votre compte patient est prêt.")
        return redirect("patient_dashboard")
    return render(request, "core/auth/signup_patient.html", {"form": form})


@_redirect_if_authenticated
def signup_doctor(request):
    form = DoctorSignupForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        from verification.services import start_verification

        start_verification(user.doctor_profile)  # automatic pre-check for the back-office
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(
            request,
            "Votre dossier a bien été transmis. Notre équipe vérifie vos justificatifs sous 48 h ouvrées.",
        )
        return redirect("doctor_dashboard")
    return render(request, "core/auth/signup_doctor.html", {"form": form})


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "core/auth/password_reset.html"
    email_template_name = "core/auth/email/password_reset_email.txt"
    subject_template_name = "core/auth/email/password_reset_subject.txt"
    form_class = StyledPasswordResetForm
    success_url = reverse_lazy("password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "core/auth/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "core/auth/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "core/auth/password_reset_complete.html"


# --------------------------------------------------------------------------
# Dashboards
# --------------------------------------------------------------------------

@login_required
def dashboard(request):
    role = get_role(request.user)
    if role == "admin":
        return redirect("backoffice:overview")
    if role == "doctor":
        return redirect("doctor_dashboard")
    if role == "patient":
        return redirect("patient_dashboard")
    messages.info(request, "Votre compte n'est associé à aucun profil MediConnect.")
    return redirect("home")


LOCKED_DOCTOR_BLOCKS = [
    {"icon": "inbox", "title": "Demandes de consultation", "empty": "Aucune demande",
     "text": "Les demandes des patients, pré-triées par l'IA, apparaîtront ici."},
    {"icon": "calendar2-week", "title": "Prochains rendez-vous", "empty": "Agenda vide",
     "text": "Vos téléconsultations planifiées s'afficheront ici."},
    {"icon": "clipboard2-pulse", "title": "Triage des patients", "empty": "Aucun dossier",
     "text": "Consultez les symptômes et le niveau d'urgence avant chaque consultation."},
    {"icon": "file-earmark-medical", "title": "Comptes rendus & ordonnances", "empty": "Aucun document",
     "text": "Rédigez et retrouvez vos comptes rendus et ordonnances."},
]


@role_required("patient")
def patient_dashboard(request):
    latest_triage = (
        TriageResult.objects.filter(session__patient=request.user).select_related("session").first()
    )
    context = {
        "active_nav": "dashboard",
        "profile": request.user.patient_profile,
        "triage": latest_triage,
        "appointments": request.user.patient_appointments.filter(
            status__in=Appointment.ACTIVE_STATUSES, start__gt=timezone.now()
        ).select_related("doctor", "specialty", "room")[:3],
        "consultations": ConsultationRoom.objects.filter(
            appointment__patient=request.user, status=ConsultationRoom.Status.CLOSED
        ).select_related("appointment__doctor", "summary")[:4],
        "prescriptions": Prescription.objects.filter(record__patient=request.user)
        .select_related("doctor").prefetch_related("items__drug")[:3],
    }
    return render(request, "core/patient/dashboard.html", context)


@role_required("doctor")
def doctor_dashboard(request):
    profile = request.user.doctor_profile
    context = {"profile": profile, "active_nav": "dashboard"}
    if not profile.is_verified:
        context["locked_blocks"] = LOCKED_DOCTOR_BLOCKS
        return render(request, "core/doctor/dashboard.html", context)

    now = timezone.now()
    today = timezone.localdate()
    qs = request.user.doctor_appointments.select_related("patient", "triage_session__result")
    pending = qs.filter(status=Appointment.Status.PENDING, start__gt=now)
    upcoming = qs.filter(status=Appointment.Status.CONFIRMED, end__gt=now)
    month = qs.filter(
        status__in=[Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED],
        start__year=today.year, start__month=today.month,
    )
    context.update({
        "requests": pending[:4],
        "appointments": upcoming[:4],
        "triaged": qs.filter(
            status__in=Appointment.ACTIVE_STATUSES, start__gt=now, triage_session__isnull=False
        ).order_by("start")[:4],
        "stats": {
            "today": upcoming.filter(start__date=today).count(),
            "pending": pending.count(),
            "month": month.count(),
        },
        "today": today,
        "has_availability": request.user.availabilities.exists(),
        "reports": ConsultationSummary.objects.filter(room__appointment__doctor=request.user)
        .select_related("room__appointment__patient")[:4],
        "live_room": ConsultationRoom.objects.filter(
            appointment__doctor=request.user, status=ConsultationRoom.Status.OPEN
        ).select_related("appointment__patient").first(),
        "waiting_rooms": ConsultationRoom.objects.filter(
            appointment__doctor=request.user, status=ConsultationRoom.Status.SCHEDULED,
            appointment__status=Appointment.Status.CONFIRMED,
            patient_last_seen__gte=timezone.now() - ONLINE_WINDOW,
        ).select_related("appointment__patient"),
    })
    return render(request, "core/doctor/dashboard.html", context)
