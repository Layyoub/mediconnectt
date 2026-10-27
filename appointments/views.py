from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.models import DoctorProfile, get_role
from core.views import role_required
from teleconsult.services import room_for
from triage.models import TriageSession

from . import notify
from .forms import AvailabilityForm, BookingForm, CancelForm, DecisionForm
from .models import Appointment, Availability, Specialty, Weekday
from .slots import BOOKING_DAYS, available_slots, next_slot

PATIENT_NAV = {"active_nav": "appointments"}


def verified_doctor_required(view):
    """Doctor pages are only usable once the back-office has verified the account."""

    @wraps(view)
    @role_required("doctor")
    def wrapper(request, *args, **kwargs):
        if not request.user.doctor_profile.is_verified:
            messages.info(request, "Cette fonctionnalité sera disponible une fois votre compte vérifié.")
            return redirect("doctor_dashboard")
        return view(request, *args, **kwargs)

    return wrapper


def _safe_next(request, default):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


def _own_completed_triage(request, pk):
    if not pk:
        return None
    return (
        TriageSession.objects.filter(pk=pk, patient=request.user, status=TriageSession.Status.COMPLETED)
        .select_related("result")
        .first()
    )


# --------------------------------------------------------------------------
# Patient
# --------------------------------------------------------------------------

@role_required("patient")
def my_appointments(request):
    now = timezone.now()
    qs = request.user.patient_appointments.select_related("doctor__doctor_profile", "specialty")
    upcoming = qs.filter(status__in=Appointment.ACTIVE_STATUSES, start__gt=now)
    history = qs.exclude(pk__in=upcoming.values("pk")).order_by("-start")
    tab = "history" if request.GET.get("tab") == "history" else "upcoming"
    context = {
        **PATIENT_NAV,
        "tab": tab,
        "upcoming": upcoming,
        "history": history,
        "appointments": history if tab == "history" else upcoming,
    }
    return render(request, "appointments/patient_list.html", context)


@role_required("patient")
def book(request):
    """Step 1: choose a specialty and a doctor."""
    triage = _own_completed_triage(request, request.GET.get("triage"))
    if "specialite" in request.GET:
        code = request.GET["specialite"]
    else:
        code = triage.result.suggested_specialty if triage else ""
    query = request.GET.get("q", "").strip()

    counts = dict(
        DoctorProfile.objects.filter(status=DoctorProfile.Status.VERIFIED)
        .values_list("specialty").annotate(n=Count("pk"))
    )
    specialties = list(Specialty.objects.filter(is_active=True))
    for s in specialties:
        s.doctor_count = counts.get(s.code, 0)
    selected = next((s for s in specialties if s.code == code), None)

    doctors = DoctorProfile.objects.filter(status=DoctorProfile.Status.VERIFIED).select_related("user")
    if selected:
        doctors = doctors.filter(specialty=selected.code)
    if query:
        doctors = doctors.filter(
            Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query) | Q(city__icontains=query)
        )
    doctors = list(doctors.order_by("user__last_name"))
    for d in doctors:
        d.next_slot = next_slot(d.user)
    # Doctors with free slots first.
    doctors.sort(key=lambda d: (d.next_slot is None, d.next_slot[0] if d.next_slot else None))

    context = {
        **PATIENT_NAV,
        "specialties": specialties,
        "selected": selected,
        "query": query,
        "doctors": doctors,
        "triage": triage,
        "booking_days": BOOKING_DAYS,
    }
    return render(request, "appointments/book.html", context)


@role_required("patient")
def doctor_slots(request, pk):
    """Step 2: pick a slot with the chosen doctor and send the request."""
    doctor = get_object_or_404(
        DoctorProfile.objects.select_related("user"), pk=pk, status=DoctorProfile.Status.VERIFIED
    )
    triage = _own_completed_triage(request, request.GET.get("triage"))
    initial = {}
    if triage:
        initial = {"triage_session": triage.pk, "reason": triage.chief_complaint}

    form = BookingForm(
        request.POST if request.method == "POST" else None,
        doctor=doctor.user, patient=request.user, initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        start, end = form.cleaned_data["slot"]
        try:
            with transaction.atomic():
                appointment = Appointment.objects.create(
                    patient=request.user,
                    doctor=doctor.user,
                    specialty=Specialty.objects.filter(code=doctor.specialty).first(),
                    triage_session=form.cleaned_data["triage_session"],
                    start=start,
                    end=end,
                    reason=form.cleaned_data["reason"],
                )
        except IntegrityError:
            form.add_error("slot", "Ce créneau vient d'être réservé, ou vous avez déjà un rendez-vous à cette heure.")
        else:
            notify.new_request(appointment)
            messages.success(
                request,
                f"Votre demande a été envoyée à Dr {doctor.user.get_full_name()}. "
                "Vous serez prévenu dès qu'il l'aura validée.",
            )
            return redirect("appointments:detail", pk=appointment.pk)

    context = {
        **PATIENT_NAV,
        "doctor": doctor,
        "days": available_slots(doctor.user),
        "form": form,
        "selected_slot": request.POST.get("slot", ""),
        "triage": triage,
        "booking_days": BOOKING_DAYS,
    }
    return render(request, "appointments/doctor_slots.html", context)


# --------------------------------------------------------------------------
# Shared (patient + doctor)
# --------------------------------------------------------------------------

def _participant_appointment(request, pk):
    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "patient__patient_profile", "doctor__doctor_profile", "specialty", "triage_session__result"
        ),
        pk=pk,
    )
    role = get_role(request.user)
    if role == "patient" and appointment.patient_id == request.user.pk:
        return appointment, role
    if role == "doctor" and appointment.doctor_id == request.user.pk and request.user.doctor_profile.is_verified:
        return appointment, role
    raise Http404


@login_required
def detail(request, pk):
    appointment, role = _participant_appointment(request, pk)
    session = appointment.triage_session
    context = {
        "appointment": appointment,
        "role": role,
        "layout": "core/patient/_layout.html" if role == "patient" else "core/doctor/_layout.html",
        "active_nav": "appointments" if role == "patient" else "requests" if appointment.status == "pending" else "agenda",
        "decision_form": DecisionForm() if role == "doctor" and appointment.status == "pending" and not appointment.is_past else None,
        "cancel_form": CancelForm() if appointment.can_cancel else None,
        "session": session,
        "room": room_for(appointment) if appointment.status in (Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED) else None,
        "entries": session.symptoms.all() if session else [],
        "red_flags": session.result.red_flags.all() if session and hasattr(session, "result") else [],
    }
    if role == "doctor":
        context["profile"] = request.user.doctor_profile
        context["patient_age"] = TriageSession.age_from_birth_date(
            appointment.patient.patient_profile.date_of_birth
        )
    return render(request, "appointments/detail.html", context)


@login_required
@require_POST
def cancel(request, pk):
    appointment, role = _participant_appointment(request, pk)
    form = CancelForm(request.POST)
    if not appointment.can_cancel:
        messages.error(request, "Ce rendez-vous ne peut plus être annulé.")
    elif form.is_valid():
        appointment.status = Appointment.Status.CANCELLED
        appointment.cancelled_by = request.user
        appointment.cancel_reason = form.cleaned_data["message"].strip()
        appointment.save(update_fields=["status", "cancelled_by", "cancel_reason"])
        notify.cancelled(appointment, request.user)
        messages.info(request, "Le rendez-vous a été annulé.")
    return redirect("appointments:detail", pk=appointment.pk)


# --------------------------------------------------------------------------
# Doctor
# --------------------------------------------------------------------------

def _doctor_context(request, active_nav, **extra):
    return {"profile": request.user.doctor_profile, "active_nav": active_nav, **extra}


@verified_doctor_required
@require_POST
def decide(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk, doctor=request.user)
    default = "appointments:requests"
    if appointment.status != Appointment.Status.PENDING or appointment.is_past:
        messages.error(request, "Cette demande n'est plus en attente.")
        return redirect(_safe_next(request, default))

    form = DecisionForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            messages.error(request, errors[0])
        return redirect(_safe_next(request, default))

    accepted = form.cleaned_data["decision"] == "accept"
    appointment.status = Appointment.Status.CONFIRMED if accepted else Appointment.Status.REFUSED
    appointment.doctor_message = form.cleaned_data["message"].strip()
    appointment.decided_at = timezone.now()
    appointment.save(update_fields=["status", "doctor_message", "decided_at"])
    if accepted:
        room_for(appointment)
    notify.decision(appointment)
    who = appointment.patient.get_full_name()
    if accepted:
        messages.success(request, f"Rendez-vous avec {who} confirmé. Il apparaît dans votre agenda.")
    else:
        messages.info(request, f"La demande de {who} a été refusée. Le patient a été prévenu.")
    return redirect(_safe_next(request, default))


@verified_doctor_required
def requests(request):
    now = timezone.now()
    qs = request.user.doctor_appointments.select_related(
        "patient__patient_profile", "triage_session__result"
    )
    pending = qs.filter(status=Appointment.Status.PENDING, start__gt=now)
    decided = qs.filter(decided_at__isnull=False).order_by("-decided_at")[:15]
    tab = "decided" if request.GET.get("tab") == "decided" else "pending"
    context = _doctor_context(
        request, "requests",
        tab=tab, pending=pending, decided=decided, decision_form=DecisionForm(),
    )
    return render(request, "appointments/doctor_requests.html", context)


@verified_doctor_required
def agenda(request):
    now = timezone.now()
    qs = request.user.doctor_appointments.select_related("patient__patient_profile", "triage_session__result")
    upcoming = list(qs.filter(status=Appointment.Status.CONFIRMED, end__gt=now))
    for a in upcoming:
        a.local_day = timezone.localdate(a.start)
    context = _doctor_context(
        request, "agenda",
        upcoming=upcoming,
        past=qs.filter(status__in=[Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED], end__lte=now)
        .order_by("-start")[:10],
        today=timezone.localdate(),
    )
    return render(request, "appointments/doctor_agenda.html", context)


@verified_doctor_required
def availability(request):
    form = AvailabilityForm(request.POST if request.method == "POST" else None, doctor=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Disponibilité ajoutée. Les patients peuvent réserver ces créneaux.")
        return redirect("appointments:availability")

    rules = list(request.user.availabilities.all())
    week = [
        {"label": label, "rules": [r for r in rules if r.weekday == value]}
        for value, label in Weekday.choices
    ]
    days = available_slots(request.user)
    context = _doctor_context(
        request, "availability",
        form=form, week=week, has_rules=bool(rules),
        free_count=sum(len(d.slots) for d in days),
        booking_days=BOOKING_DAYS,
    )
    return render(request, "appointments/doctor_availability.html", context)


@verified_doctor_required
@require_POST
def delete_availability(request, pk):
    rule = get_object_or_404(Availability, pk=pk, doctor=request.user)
    rule.delete()
    messages.info(request, "Disponibilité supprimée. Les rendez-vous déjà pris sont conservés.")
    return redirect("appointments:availability")
