"""Appointment notifications: in-app (teleconsult.Notification) + e-mail.

E-mails use the console backend in development.
"""

import logging

from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

logger = logging.getLogger(__name__)


def _when(appointment):
    local = timezone.localtime(appointment.start)
    return f"{date_format(local, 'l j F Y')} à {local:%H:%M}"


def _send(to, subject, body):
    if not to:
        return
    try:
        send_mail(f"[MediConnect] {subject}", body, None, [to])
    except Exception:  # an e-mail failure must never block the booking
        logger.exception("Envoi d'e-mail impossible")


def _in_app(user, kind, title, body, appointment):
    from teleconsult.services import notify

    notify(user, kind, title, body, reverse("appointments:detail", args=[appointment.pk]))


def new_request(appointment):
    patient = appointment.patient.get_full_name()
    _in_app(appointment.doctor, "request", "Nouvelle demande de rendez-vous",
            f"{patient} · {_when(appointment)}", appointment)
    _send(
        appointment.doctor.email,
        "Nouvelle demande de rendez-vous",
        f"Bonjour Dr {appointment.doctor.last_name},\n\n"
        f"{patient} demande une téléconsultation le {_when(appointment)}.\n"
        f"Motif : {appointment.reason}\n\n"
        "Connectez-vous à votre espace médecin pour accepter ou refuser la demande.\n",
    )


def decision(appointment):
    doctor = appointment.doctor.get_full_name()
    if appointment.status == appointment.Status.CONFIRMED:
        kind, subject = "confirmed", "Votre rendez-vous est confirmé"
        text = f"Dr {doctor} a confirmé votre téléconsultation du {_when(appointment)}."
    else:
        kind, subject = "refused", "Votre demande de rendez-vous n'a pas été acceptée"
        text = (f"Dr {doctor} ne peut pas vous recevoir le {_when(appointment)}.\n"
                "Vous pouvez choisir un autre créneau ou un autre médecin depuis votre espace patient.")
    _in_app(appointment.patient, kind, subject, text.split("\n")[0], appointment)
    if appointment.doctor_message:
        text += f"\n\nMessage du médecin : {appointment.doctor_message}"
    _send(appointment.patient.email, subject, f"Bonjour {appointment.patient.first_name},\n\n{text}\n")


def cancelled(appointment, by):
    by_patient = by == appointment.patient
    other = appointment.doctor if by_patient else appointment.patient
    who = "par le patient" if by_patient else "par le médecin"
    _in_app(other, "cancelled", "Rendez-vous annulé", f"{_when(appointment)} · annulé {who}", appointment)
    _send(
        other.email,
        "Rendez-vous annulé",
        f"Bonjour,\n\nLe rendez-vous du {_when(appointment)} a été annulé {who}.\n"
        + (f"Motif : {appointment.cancel_reason}\n" if appointment.cancel_reason else ""),
    )
