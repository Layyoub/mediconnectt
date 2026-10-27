"""Back-office views for MediConnect staff, separate from the public site."""

import mimetypes
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from triage.models import RedFlagRule, TriageLevel, TriageResult, TriageSession
from verification.models import DoctorVerification
from verification.services import start_verification

from .forms import DoctorDecisionForm
from .models import DoctorProfile, PatientProfile

User = get_user_model()

DOCUMENT_FIELDS = {
    "diplome": "diploma",
    "ordre": "license_document",
}


def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_staff:
            messages.error(request, "Accès réservé à l'équipe MediConnect.")
            return redirect("dashboard")
        return view(request, *args, **kwargs)

    return wrapper


def _documents(doctor):
    labels = {
        "diplome": "Diplôme de médecine",
        "ordre": "Attestation d'inscription à l'Ordre",
    }
    return [
        (kind, labels[kind], getattr(doctor, field))
        for kind, field in DOCUMENT_FIELDS.items()
        if getattr(doctor, field)
    ]


def _detail_context(doctor, form):
    return {
        "section": "doctors",
        "pending_count": _pending_count(),
        "doctor": doctor,
        "documents": _documents(doctor),
        "form": form,
        "verification": doctor.verifications.select_related("triggered_by").first(),
    }


def _pending_count():
    return DoctorProfile.objects.filter(status=DoctorProfile.Status.PENDING).count()


@staff_required
def overview(request):
    doctors = DoctorProfile.objects.select_related("user")
    context = {
        "section": "overview",
        "pending_count": _pending_count(),
        "stats": {
            "patients": PatientProfile.objects.count(),
            "doctors": doctors.count(),
            "verified": doctors.filter(status=DoctorProfile.Status.VERIFIED).count(),
            "pending": doctors.filter(status=DoctorProfile.Status.PENDING).count(),
        },
        "pending_doctors": doctors.filter(status=DoctorProfile.Status.PENDING)[:5],
        "recent_patients": PatientProfile.objects.select_related("user").order_by("-created_at")[:5],
    }
    return render(request, "core/backoffice/overview.html", context)


@staff_required
def doctor_list(request):
    status = request.GET.get("statut", "")
    query = request.GET.get("q", "").strip()
    latest = DoctorVerification.objects.filter(doctor=OuterRef("pk"), status=DoctorVerification.Status.DONE)
    doctors = DoctorProfile.objects.select_related("user").annotate(
        ai_risk=Subquery(latest.values("risk")[:1])
    )
    if status in DoctorProfile.Status.values:
        doctors = doctors.filter(status=status)
    if query:
        doctors = doctors.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(license_number__icontains=query)
            | Q(city__icontains=query)
        )
    context = {
        "section": "doctors",
        "pending_count": _pending_count(),
        "doctors": doctors,
        "status": status,
        "query": query,
        "status_choices": DoctorProfile.Status.choices,
    }
    return render(request, "core/backoffice/doctor_list.html", context)


@staff_required
def doctor_detail(request, pk):
    doctor = get_object_or_404(DoctorProfile.objects.select_related("user"), pk=pk)
    return render(request, "core/backoffice/doctor_detail.html", _detail_context(doctor, DoctorDecisionForm()))


@staff_required
@require_POST
def doctor_decision(request, pk):
    doctor = get_object_or_404(DoctorProfile, pk=pk)
    form = DoctorDecisionForm(request.POST)
    if not form.is_valid():
        return render(request, "core/backoffice/doctor_detail.html", _detail_context(doctor, form), status=400)
    if form.cleaned_data["decision"] == "approve":
        doctor.status = DoctorProfile.Status.VERIFIED
        doctor.rejection_reason = ""
        messages.success(request, f"{doctor} est maintenant vérifié.")
    else:
        doctor.status = DoctorProfile.Status.REJECTED
        doctor.rejection_reason = form.cleaned_data["reason"].strip()
        messages.success(request, f"Le dossier de {doctor} a été refusé.")
    doctor.reviewed_at = timezone.now()
    doctor.save(update_fields=["status", "rejection_reason", "reviewed_at"])
    return redirect("backoffice:doctor_detail", pk=doctor.pk)


@staff_required
@require_POST
def doctor_verify(request, pk):
    """Run the automatic analysis again (e.g. after new documents)."""
    doctor = get_object_or_404(DoctorProfile.objects.select_related("user"), pk=pk)
    if any(v.is_running for v in doctor.verifications.filter(status=DoctorVerification.Status.RUNNING)):
        messages.info(request, "Une analyse est déjà en cours.")
    else:
        start_verification(doctor, by=request.user)
        messages.success(request, "Analyse lancée. Le résultat s'affiche dans quelques secondes.")
    return redirect("backoffice:doctor_detail", pk=doctor.pk)


@staff_required
def doctor_document(request, pk, kind):
    """Serve a credential document to staff only (never through a public URL)."""
    field = DOCUMENT_FIELDS.get(kind)
    if field is None:
        raise Http404
    doctor = get_object_or_404(DoctorProfile, pk=pk)
    document = getattr(doctor, field)
    if not document:
        raise Http404
    try:
        handle = document.open("rb")
    except FileNotFoundError:
        raise Http404
    content_type = mimetypes.guess_type(document.name)[0] or "application/octet-stream"
    return FileResponse(handle, content_type=content_type)


@staff_required
def patient_list(request):
    query = request.GET.get("q", "").strip()
    patients = PatientProfile.objects.select_related("user").order_by("-created_at")
    if query:
        patients = patients.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(phone__icontains=query)
        )
    return render(
        request,
        "core/backoffice/patient_list.html",
        {"section": "patients", "pending_count": _pending_count(), "patients": patients, "query": query},
    )


@staff_required
def triage_overview(request):
    results = TriageResult.objects.select_related("session__patient")
    counts = {row["level"]: row["n"] for row in results.values("level").annotate(n=Count("id"))}
    total = sum(counts.values())
    levels = [
        {
            "code": code,
            "label": label,
            "count": counts.get(code, 0),
            "percent": round(100 * counts.get(code, 0) / total) if total else 0,
        }
        for code, label in TriageLevel.choices
    ]
    context = {
        "section": "triage",
        "pending_count": _pending_count(),
        "total": total,
        "in_progress": TriageSession.objects.filter(status=TriageSession.Status.IN_PROGRESS).count(),
        "levels": levels,
        "recent": results[:8],
        "rules": RedFlagRule.objects.annotate(times=Count("results")),
    }
    return render(request, "core/backoffice/triage.html", context)


@staff_required
@require_POST
def toggle_rule(request, pk):
    rule = get_object_or_404(RedFlagRule, pk=pk)
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active"])
    state = "activée" if rule.is_active else "désactivée"
    messages.success(request, f"La règle « {rule.name} » est {state}.")
    return redirect("backoffice:triage")


@staff_required
def appointments_overview(request):
    from appointments.models import Appointment, Specialty

    appointments = Appointment.objects.select_related("patient", "doctor", "specialty")
    status = request.GET.get("statut", "")
    if status not in Appointment.Status.values:
        status = ""
    counts = {row["status"]: row["n"] for row in Appointment.objects.values("status").annotate(n=Count("id"))}
    doctor_counts = dict(
        DoctorProfile.objects.filter(status=DoctorProfile.Status.VERIFIED)
        .values_list("specialty").annotate(n=Count("pk"))
    )
    specialties = list(Specialty.objects.annotate(booked=Count("appointments")).order_by("position", "name"))
    for s in specialties:
        s.doctor_count = doctor_counts.get(s.code, 0)
    context = {
        "section": "appointments",
        "pending_count": _pending_count(),
        "total": sum(counts.values()),
        "statuses": [{"code": c, "label": l, "count": counts.get(c, 0)} for c, l in Appointment.Status.choices],
        "status": status,
        "appointments": (appointments.filter(status=status) if status else appointments).order_by("-created_at")[:25],
        "specialties": specialties,
    }
    return render(request, "core/backoffice/appointments.html", context)
