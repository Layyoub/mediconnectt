"""Room lifecycle and in-app notifications."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment

from .models import ChatMessage, ConsultationRoom, Notification


def notify(user, kind, title, body="", url=""):
    return Notification.objects.create(user=user, kind=kind, title=title, body=body[:300], url=url)


def room_for(appointment):
    """The room of a confirmed (or completed) appointment, created on first use."""
    room, _ = ConsultationRoom.objects.get_or_create(appointment=appointment)
    return room


def system_message(room, text):
    return ChatMessage.objects.create(room=room, kind=ChatMessage.Kind.SYSTEM, body=text)


def patient_entered(room):
    """Patient is in the waiting room: tell the doctor (once, until read)."""
    url = reverse("teleconsult:room", args=[room.code])
    doctor = room.appointment.doctor
    if not Notification.objects.filter(user=doctor, kind=Notification.Kind.WAITING, url=url, is_read=False).exists():
        notify(
            doctor, Notification.Kind.WAITING,
            f"{room.appointment.patient.get_full_name()} vous attend",
            "Le patient est dans la salle d'attente. Entrez dans la salle pour démarrer la consultation.",
            url,
        )


def start_room(room, by):
    """The doctor entered the room: the consultation starts."""
    if room.status != ConsultationRoom.Status.SCHEDULED:
        return
    room.open()
    system_message(room, f"Dr {by.get_full_name()} a rejoint la salle : la consultation commence.")
    notify(
        room.appointment.patient, Notification.Kind.ROOM_OPEN,
        "Votre téléconsultation a commencé",
        f"Dr {by.get_full_name()} vous attend dans la salle.",
        reverse("teleconsult:room", args=[room.code]),
    )


@transaction.atomic
def close_room(room, summary):
    """Save the report, close the room and mark the appointment as completed."""
    summary.room = room
    summary.save()
    if room.is_closed:
        return
    now = timezone.now()
    room.status = ConsultationRoom.Status.CLOSED
    room.opened_at = room.opened_at or now
    room.closed_at = now
    room.save(update_fields=["status", "opened_at", "closed_at"])
    Appointment.objects.filter(pk=room.appointment_id).update(status=Appointment.Status.COMPLETED)
    system_message(room, "La consultation est terminée. Le compte rendu est disponible.")
    notify(
        room.appointment.patient, Notification.Kind.SUMMARY,
        "Compte rendu disponible",
        f"Dr {room.appointment.doctor.get_full_name()} a rédigé le compte rendu de votre consultation.",
        reverse("teleconsult:summary", args=[room.code]),
    )
