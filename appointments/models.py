from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import DoctorProfile


class Specialty(models.Model):
    """A medical specialty patients can book. `code` matches DoctorProfile.specialty."""

    code = models.CharField("code", max_length=30, unique=True, choices=DoctorProfile.Specialty.choices)
    name = models.CharField("nom", max_length=80)
    icon = models.CharField("icône (Bootstrap Icons)", max_length=40, default="heart-pulse")
    description = models.CharField("description", max_length=200, blank=True)
    is_active = models.BooleanField("proposée aux patients", default=True)
    position = models.PositiveSmallIntegerField("ordre d'affichage", default=10)

    class Meta:
        verbose_name = "spécialité"
        verbose_name_plural = "spécialités"
        ordering = ["position", "name"]

    def __str__(self):
        return self.name

    def verified_doctors(self):
        return DoctorProfile.objects.filter(
            specialty=self.code, status=DoctorProfile.Status.VERIFIED
        ).select_related("user")


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Lundi"
    TUESDAY = 1, "Mardi"
    WEDNESDAY = 2, "Mercredi"
    THURSDAY = 3, "Jeudi"
    FRIDAY = 4, "Vendredi"
    SATURDAY = 5, "Samedi"
    SUNDAY = 6, "Dimanche"


class Availability(models.Model):
    """A weekly time range during which a doctor accepts teleconsultations."""

    SLOT_CHOICES = [(15, "15 min"), (20, "20 min"), (30, "30 min"), (45, "45 min"), (60, "1 h")]

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="availabilities",
        verbose_name="médecin",
    )
    weekday = models.PositiveSmallIntegerField("jour", choices=Weekday.choices)
    start_time = models.TimeField("début")
    end_time = models.TimeField("fin")
    slot_minutes = models.PositiveSmallIntegerField("durée d'un créneau", choices=SLOT_CHOICES, default=30)
    is_active = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "disponibilité"
        verbose_name_plural = "disponibilités"
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def clean(self):
        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValidationError({"end_time": "L'heure de fin doit être après l'heure de début."})
            overlap = Availability.objects.filter(
                doctor_id=self.doctor_id, weekday=self.weekday,
                start_time__lt=self.end_time, end_time__gt=self.start_time,
            ).exclude(pk=self.pk)
            if self.doctor_id and overlap.exists():
                raise ValidationError("Cette plage chevauche une disponibilité existante le même jour.")


class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente de validation"
        CONFIRMED = "confirmed", "Confirmé"
        REFUSED = "refused", "Refusé"
        CANCELLED = "cancelled", "Annulé"
        COMPLETED = "completed", "Terminé"

    ACTIVE_STATUSES = [Status.PENDING, Status.CONFIRMED]

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="patient_appointments",
        verbose_name="patient",
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="doctor_appointments",
        verbose_name="médecin",
    )
    specialty = models.ForeignKey(
        Specialty, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointments",
        verbose_name="spécialité",
    )
    triage_session = models.ForeignKey(
        "triage.TriageSession", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appointments", verbose_name="évaluation partagée",
    )
    start = models.DateTimeField("début")
    end = models.DateTimeField("fin")
    reason = models.TextField("motif")
    status = models.CharField("statut", max_length=12, choices=Status.choices, default=Status.PENDING)
    doctor_message = models.TextField("message du médecin", blank=True)
    cancel_reason = models.TextField("motif de l'annulation", blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="annulé par",
    )
    created_at = models.DateTimeField("demandé le", auto_now_add=True)
    decided_at = models.DateTimeField("traité le", null=True, blank=True)

    class Meta:
        verbose_name = "rendez-vous"
        verbose_name_plural = "rendez-vous"
        ordering = ["start"]
        constraints = [
            # A slot can only be held by one active appointment.
            models.UniqueConstraint(
                fields=["doctor", "start"], condition=Q(status__in=["pending", "confirmed"]),
                name="unique_active_doctor_slot",
            ),
            models.UniqueConstraint(
                fields=["patient", "start"], condition=Q(status__in=["pending", "confirmed"]),
                name="unique_active_patient_slot",
            ),
        ]

    def __str__(self):
        return f"{self.patient.get_full_name()} → Dr {self.doctor.get_full_name()} · {timezone.localtime(self.start):%d/%m/%Y %H:%M}"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_past(self):
        return self.start <= timezone.now()

    @property
    def is_expired(self):
        """A request the doctor never answered before its time."""
        return self.status == self.Status.PENDING and self.is_past

    @property
    def can_cancel(self):
        return self.is_active and not self.is_past

    @property
    def duration_minutes(self):
        return int((self.end - self.start).total_seconds() // 60)

    @property
    def status_key(self):
        """Status used for display (adds 'expired')."""
        return "expired" if self.is_expired else self.status

    @property
    def status_label(self):
        return "Expiré (sans réponse)" if self.is_expired else self.get_status_display()
