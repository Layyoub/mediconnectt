import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from appointments.models import Appointment
from core.models import get_role
from records import access
from records.ai_analysis import start_analysis
from records.forms import SharedDocumentForm
from records.models import AccessLog, MedicalRecord
from triage.models import TriageSession

from .forms import SummaryForm
from .models import ChatMessage, ConsultationRoom, ConsultationSummary, Notification
from .services import close_room, notify, patient_entered, room_for, start_room

SIGNAL_TYPES = {"hello", "offer", "answer", "candidate", "bye"}
SIGNAL_MAX_BYTES = 20_000
SIGNAL_TTL = 90  # seconds a signal waits to be read
CURSOR_TTL = 3600  # sequence counters


def _layout_context(request, role, active_nav="consultations"):
    ctx = {
        "role": role,
        "active_nav": active_nav,
        "layout": "core/patient/_layout.html" if role == "patient" else "core/doctor/_layout.html",
    }
    if role == "doctor":
        ctx["profile"] = request.user.doctor_profile
    return ctx


def _participant_room(request, code):
    """Room of a confirmed/completed appointment the user takes part in (404 otherwise)."""
    room = get_object_or_404(
        ConsultationRoom.objects.select_related(
            "appointment__patient__patient_profile", "appointment__doctor__doctor_profile",
            "appointment__specialty", "appointment__triage_session__result",
        ),
        code=code,
        appointment__status__in=[Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED],
    )
    role = room.role_of(request.user)
    if role is None or (role == "doctor" and not request.user.doctor_profile.is_verified):
        raise Http404
    return room, role


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@login_required
def consultation_list(request):
    role = get_role(request.user)
    if role not in ("patient", "doctor"):
        return redirect("dashboard")
    if role == "doctor" and not request.user.doctor_profile.is_verified:
        return redirect("doctor_dashboard")

    field = "patient" if role == "patient" else "doctor"
    confirmed = Appointment.objects.filter(
        **{field: request.user}, status=Appointment.Status.CONFIRMED, end__gt=timezone.now()
    ).select_related("patient", "doctor", "specialty")
    upcoming = [(a, room_for(a)) for a in confirmed]
    past = ConsultationRoom.objects.filter(
        **{f"appointment__{field}": request.user}, status=ConsultationRoom.Status.CLOSED
    ).select_related("appointment__patient", "appointment__doctor", "appointment__specialty", "summary")
    context = {**_layout_context(request, role), "upcoming": upcoming, "past": past}
    return render(request, "teleconsult/list.html", context)


@login_required
def room(request, code):
    room, role = _participant_room(request, code)
    if room.status == ConsultationRoom.Status.SCHEDULED:
        if role == "doctor":
            # The consultation starts when the doctor enters the room.
            start_room(room, request.user)
        else:
            # Patient waits in the waiting room until the doctor arrives.
            room.mark_seen("patient")
            patient_entered(room)
    appointment = room.appointment
    other = room.other_user(request.user)
    session = appointment.triage_session
    context = {
        **_layout_context(request, role),
        "room": room,
        "appointment": appointment,
        "other": other,
        "other_label": f"Dr {other.get_full_name()}" if role == "patient" else other.get_full_name(),
        "session": session,
        "entries": session.symptoms.all() if session and role == "doctor" else [],
        "red_flags": session.result.red_flags.all() if session and role == "doctor" and hasattr(session, "result") else [],
        "patient_age": TriageSession.age_from_birth_date(appointment.patient.patient_profile.date_of_birth),
        "transcript": room.messages.select_related("sender", "document") if room.is_closed else None,
        "record": MedicalRecord.objects.filter(patient=appointment.patient).first() if role == "doctor" else None,
    }
    return render(request, "teleconsult/room.html", context)


@login_required
def finish(request, code):
    """Doctor writes the report; saving it closes the consultation."""
    room, role = _participant_room(request, code)
    if role != "doctor":
        raise Http404
    instance = getattr(room, "summary", None)
    if room.status == ConsultationRoom.Status.SCHEDULED and instance is None:
        messages.info(request, "Entrez d'abord dans la salle pour démarrer la consultation.")
        return redirect("teleconsult:room", code=room.code)
    form = SummaryForm(request.POST if request.method == "POST" else None, instance=instance)
    if request.method == "POST" and form.is_valid():
        summary = form.save(commit=False)
        summary.author = request.user
        close_room(room, summary)
        messages.success(request, "Compte rendu enregistré. La consultation est terminée et le patient a été prévenu.")
        return redirect("teleconsult:summary", code=room.code)
    context = {**_layout_context(request, role), "room": room, "appointment": room.appointment, "form": form}
    return render(request, "teleconsult/finish.html", context)


@login_required
def summary(request, code):
    room, role = _participant_room(request, code)
    summary = getattr(room, "summary", None)
    if summary is None:
        raise Http404
    patient = room.appointment.patient
    context = {
        **_layout_context(request, role),
        "room": room,
        "appointment": room.appointment,
        "summary": summary,
        "patient_age": TriageSession.age_from_birth_date(patient.patient_profile.date_of_birth),
        "prescriptions": room.appointment.prescriptions.select_related("doctor").prefetch_related("items__drug"),
    }
    return render(request, "teleconsult/summary.html", context)


# --------------------------------------------------------------------------
# JSON endpoints used by the room page (polling)
# --------------------------------------------------------------------------

def _room_state(room, role):
    return {
        "status": room.status,
        "other_online": room.other_online(role),
    }


@login_required
@require_http_methods(["GET", "POST"])
def chat(request, code):
    room, role = _participant_room(request, code)

    if request.method == "POST":
        if not room.is_open:
            return JsonResponse({"error": "La salle n'est pas ouverte."}, status=409)
        body = (request.POST.get("body") or "").strip()
        if not body:
            return JsonResponse({"error": "Message vide."}, status=400)
        if len(body) > 2000:
            return JsonResponse({"error": "Message trop long (2000 caractères maximum)."}, status=400)
        msg = ChatMessage.objects.create(room=room, sender=request.user, body=body)
        other = room.other_user(request.user)
        room_url = reverse("teleconsult:room", args=[room.code])
        # Notify only if the other participant isn't in the room, and only once.
        if not room.other_online(role) and not Notification.objects.filter(
            user=other, kind=Notification.Kind.MESSAGE, is_read=False, url=room_url
        ).exists():
            notify(other, Notification.Kind.MESSAGE, "Nouveau message dans votre consultation", body[:120], room_url)
        return JsonResponse({"message": msg.as_json(request.user), **_room_state(room, role)})

    room.mark_seen(role)
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    msgs = room.messages.filter(pk__gt=after).select_related("sender", "room__appointment", "document")[:200]
    return JsonResponse({"messages": [m.as_json(request.user) for m in msgs], **_room_state(room, role)})


@login_required
@require_POST
def share_document(request, code):
    """Attach a document (lab results…) during the consultation: saved in the patient's record."""
    room, role = _participant_room(request, code)
    if not room.is_open:
        return JsonResponse({"error": "La consultation n'est pas en cours."}, status=409)
    form = SharedDocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        errors = [e for field_errors in form.errors.values() for e in field_errors]
        return JsonResponse({"error": " ".join(errors) or "Fichier refusé."}, status=400)
    appointment = room.appointment
    record = MedicalRecord.for_patient(appointment.patient)
    document = form.save(commit=False)
    upload = form.cleaned_data["file"]
    document.record = record
    document.uploaded_by = request.user
    document.appointment = appointment
    document.original_name = upload.name[:200]
    document.size = upload.size
    document.save()
    access.log(request, record, AccessLog.Action.DOCUMENT_ADD, document.title)
    msg = ChatMessage.objects.create(
        room=room, sender=request.user, kind=ChatMessage.Kind.DOCUMENT, body=document.title, document=document
    )
    # The AI report is prepared right away so it is ready when the doctor opens it.
    start_analysis(document)
    return JsonResponse({"message": msg.as_json(request.user), **_room_state(room, role)})


def _signal_key(room, role):
    return f"rtc:{room.pk}:{role}"


@login_required
@require_http_methods(["GET", "POST"])
def signal(request, code):
    """WebRTC signaling mailbox (offer / answer / ICE candidates) kept in the cache."""
    room, role = _participant_room(request, code)
    if not room.is_open:
        return JsonResponse({"signals": [], "status": room.status})

    if request.method == "POST":
        if len(request.body) > SIGNAL_MAX_BYTES:
            return JsonResponse({"error": "Signal trop volumineux."}, status=400)
        try:
            payload = json.loads(request.body)
        except ValueError:
            return JsonResponse({"error": "JSON invalide."}, status=400)
        if not isinstance(payload, dict) or payload.get("type") not in SIGNAL_TYPES:
            return JsonResponse({"error": "Type de signal inconnu."}, status=400)
        # Each signal gets its own key and an atomic sequence number, so
        # simultaneous POSTs (ICE candidates arrive in bursts) are never lost.
        base = _signal_key(room, "doctor" if role == "patient" else "patient")
        cache.add(f"{base}:seq", 0, CURSOR_TTL)
        try:
            n = cache.incr(f"{base}:seq")
        except ValueError:  # counter expired between add() and incr()
            cache.set(f"{base}:seq", 1, CURSOR_TTL)
            n = 1
        cache.touch(f"{base}:seq", CURSOR_TTL)
        cache.set(f"{base}:{n}", {"type": payload["type"], "data": payload.get("data")}, SIGNAL_TTL)
        return JsonResponse({"ok": True})

    base = _signal_key(room, role)
    last_read = cache.get(f"{base}:read", 0)
    newest = cache.get(f"{base}:seq", 0)
    if newest < last_read:  # counter was reset after expiry
        last_read = 0
    found = cache.get_many([f"{base}:{i}" for i in range(last_read + 1, newest + 1)])
    signals = [found[k] for k in (f"{base}:{i}" for i in range(last_read + 1, newest + 1)) if k in found]
    cache.set(f"{base}:read", newest, CURSOR_TTL)
    return JsonResponse({"signals": signals, "status": room.status})


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

@login_required
def notification_list(request):
    role = get_role(request.user)
    notifications = list(request.user.notifications.all()[:50])
    context = {"notifications": notifications, "active_nav": "notifications"}
    if role == "patient":
        context["layout"] = "core/patient/_layout.html"
    elif role == "doctor":
        context["layout"] = "core/doctor/_layout.html"
        context["profile"] = request.user.doctor_profile
    else:
        context["layout"] = "core/backoffice/_layout.html"
    return render(request, "teleconsult/notifications.html", context)


@login_required
def notification_open(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    if notification.url.startswith("/") and not notification.url.startswith("//"):
        return redirect(notification.url)
    return redirect("teleconsult:notifications")


@login_required
@require_POST
def notification_read_all(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect("teleconsult:notifications")
