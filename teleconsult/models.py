import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment

ONLINE_WINDOW = timedelta(seconds=12)  # "online" if the page polled recently


def _room_code():
    return secrets.token_urlsafe(12)


class ConsultationRoom(models.Model):
    """The virtual room of a confirmed appointment (video + chat)."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Programmée"
        OPEN = "open", "En cours"
        CLOSED = "closed", "Terminée"

    appointment = models.OneToOneField(
        Appointment, on_delete=models.CASCADE, related_name="room", verbose_name="rendez-vous"
    )
    code = models.CharField("code d'accès", max_length=32, unique=True, default=_room_code, editable=False)
    status = models.CharField("statut", max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    opened_at = models.DateTimeField("ouverte le", null=True, blank=True)
    closed_at = models.DateTimeField("fermée le", null=True, blank=True)
    patient_last_seen = models.DateTimeField(null=True, blank=True, editable=False)
    doctor_last_seen = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "salle de consultation"
        verbose_name_plural = "salles de consultation"
        ordering = ["-appointment__start"]

    def __str__(self):
        return f"Salle · {self.appointment}"

    # --- participants -------------------------------------------------------
    def role_of(self, user):
        if user.pk == self.appointment.patient_id:
            return "patient"
        if user.pk == self.appointment.doctor_id:
            return "doctor"
        return None

    def other_user(self, user):
        a = self.appointment
        return a.doctor if user.pk == a.patient_id else a.patient

    def other_online(self, role):
        seen = self.doctor_last_seen if role == "patient" else self.patient_last_seen
        return bool(seen and timezone.now() - seen < ONLINE_WINDOW)

    def mark_seen(self, role):
        field = f"{role}_last_seen"
        setattr(self, field, timezone.now())
        ConsultationRoom.objects.filter(pk=self.pk).update(**{field: getattr(self, field)})

    @property
    def patient_waiting(self):
        """Patient is in the waiting room (page polled recently) and the doctor hasn't started."""
        return (
            self.status == self.Status.SCHEDULED
            and bool(self.patient_last_seen and timezone.now() - self.patient_last_seen < ONLINE_WINDOW)
        )

    # --- lifecycle ----------------------------------------------------------
    # The patient can enter the waiting room at any time; the consultation
    # only starts when the doctor enters the room (services.start_room).
    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    @property
    def is_closed(self):
        return self.status == self.Status.CLOSED

    def open(self):
        self.status = self.Status.OPEN
        self.opened_at = timezone.now()
        self.save(update_fields=["status", "opened_at"])

    @property
    def duration_minutes(self):
        if not self.opened_at:
            return None
        end = self.closed_at or timezone.now()
        return max(1, int((end - self.opened_at).total_seconds() // 60))


class ChatMessage(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Message"
        SYSTEM = "system", "Événement"
        DOCUMENT = "document", "Document partagé"

    room = models.ForeignKey(ConsultationRoom, on_delete=models.CASCADE, related_name="messages", verbose_name="salle")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="chat_messages", verbose_name="auteur",
    )
    kind = models.CharField("type", max_length=10, choices=Kind.choices, default=Kind.TEXT)
    body = models.TextField("message", max_length=2000)
    document = models.ForeignKey(
        "records.MedicalDocument", on_delete=models.SET_NULL, null=True, blank=True, related_name="chat_messages",
        verbose_name="document partagé",
    )
    created_at = models.DateTimeField("envoyé le", auto_now_add=True)

    class Meta:
        verbose_name = "message"
        verbose_name_plural = "messages"
        ordering = ["id"]

    def __str__(self):
        return self.body[:50]

    def as_json(self, viewer):
        local = timezone.localtime(self.created_at)
        is_doctor = self.sender_id == self.room.appointment.doctor_id
        data = {
            "id": self.pk,
            "kind": self.kind,
            "body": self.body,
            "mine": self.sender_id == viewer.pk,
            "sender": (f"Dr {self.sender.get_full_name()}" if is_doctor else self.sender.get_full_name()) if self.sender else "",
            "time": f"{local:%H:%M}",
        }
        if self.kind == self.Kind.DOCUMENT:
            doc = self.document
            data["document"] = None if doc is None else {
                "title": doc.title,
                "kind": doc.get_kind_display(),
                "info": f"{doc.extension} · {doc.size_label}",
                "url": reverse("records:document", args=[doc.pk]),
                # The AI report is for the doctor only.
                "analysis_url": reverse("records:document_analysis", args=[doc.pk])
                if viewer.pk == self.room.appointment.doctor_id else None,
                "analysis_api": reverse("records:document_analysis_api", args=[doc.pk])
                if viewer.pk == self.room.appointment.doctor_id else None,
            }
        return data


class ConsultationSummary(models.Model):
    """Report written by the doctor at the end of the teleconsultation."""

    room = models.OneToOneField(ConsultationRoom, on_delete=models.CASCADE, related_name="summary", verbose_name="salle")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+", verbose_name="médecin"
    )
    diagnosis = models.TextField("diagnostic / hypothèse")
    observations = models.TextField("observations", blank=True)
    recommendations = models.TextField("conduite à tenir et conseils")
    follow_up = models.CharField("suivi", max_length=200, blank=True)
    created_at = models.DateTimeField("rédigé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "compte rendu"
        verbose_name_plural = "comptes rendus"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Compte rendu · {self.room.appointment}"


class Notification(models.Model):
    class Kind(models.TextChoices):
        REQUEST = "request", "Nouvelle demande"
        CONFIRMED = "confirmed", "Rendez-vous confirmé"
        REFUSED = "refused", "Demande refusée"
        CANCELLED = "cancelled", "Rendez-vous annulé"
        ROOM_OPEN = "room_open", "Consultation ouverte"
        WAITING = "waiting", "Patient en salle d'attente"
        MESSAGE = "message", "Nouveau message"
        SUMMARY = "summary", "Compte rendu disponible"
        PRESCRIPTION = "prescription", "Nouvelle ordonnance"
        RECORD = "record", "Dossier médical mis à jour"

    ICONS = {
        "request": "inbox", "confirmed": "check2-circle", "refused": "x-circle", "cancelled": "slash-circle",
        "room_open": "camera-video", "waiting": "hourglass-split", "message": "chat-dots", "summary": "file-earmark-text",
        "prescription": "capsule", "record": "folder2-open",
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications", verbose_name="destinataire"
    )
    kind = models.CharField("type", max_length=12, choices=Kind.choices)
    title = models.CharField("titre", max_length=120)
    body = models.CharField("texte", max_length=300, blank=True)
    url = models.CharField("lien", max_length=200, blank=True)
    is_read = models.BooleanField("lue", default=False)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read"])]

    def __str__(self):
        return self.title

    @property
    def icon(self):
        return self.ICONS.get(self.kind, "bell")
