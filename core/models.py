from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

DOCUMENT_VALIDATOR = FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])


class PatientProfile(models.Model):
    class Gender(models.TextChoices):
        FEMALE = "F", "Femme"
        MALE = "M", "Homme"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="patient_profile"
    )
    phone = models.CharField("téléphone", max_length=20)
    date_of_birth = models.DateField("date de naissance", null=True, blank=True)
    gender = models.CharField("genre", max_length=1, choices=Gender.choices, blank=True)
    created_at = models.DateTimeField("inscrit le", auto_now_add=True)

    class Meta:
        verbose_name = "profil patient"
        verbose_name_plural = "profils patients"

    def __str__(self):
        return self.user.get_full_name() or self.user.email


class DoctorProfile(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente de vérification"
        VERIFIED = "verified", "Vérifié"
        REJECTED = "rejected", "Refusé"

    class Specialty(models.TextChoices):
        GENERAL = "general", "Médecine générale"
        CARDIOLOGY = "cardiology", "Cardiologie"
        DERMATOLOGY = "dermatology", "Dermatologie"
        PEDIATRICS = "pediatrics", "Pédiatrie"
        GYNECOLOGY = "gynecology", "Gynécologie"
        PSYCHIATRY = "psychiatry", "Psychiatrie"
        ENT = "ent", "ORL"
        OPHTHALMOLOGY = "ophthalmology", "Ophtalmologie"
        PULMONOLOGY = "pulmonology", "Pneumologie"
        OTHER = "other", "Autre spécialité"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="doctor_profile"
    )
    phone = models.CharField("téléphone", max_length=20)
    specialty = models.CharField("spécialité", max_length=30, choices=Specialty.choices)
    license_number = models.CharField("numéro d'inscription à l'Ordre", max_length=30, unique=True)
    years_experience = models.PositiveSmallIntegerField("années d'expérience", default=0)
    city = models.CharField("ville d'exercice", max_length=100)
    practice_address = models.CharField("adresse du cabinet", max_length=255, blank=True)
    bio = models.TextField("présentation", blank=True)

    diploma = models.FileField(
        "diplôme de médecine", upload_to="justificatifs/diplomes/", validators=[DOCUMENT_VALIDATOR]
    )
    license_document = models.FileField(
        "attestation d'inscription à l'Ordre",
        upload_to="justificatifs/ordre/",
        validators=[DOCUMENT_VALIDATOR],
    )

    status = models.CharField("statut", max_length=10, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField("motif du refus", blank=True)
    submitted_at = models.DateTimeField("soumis le", auto_now_add=True)
    reviewed_at = models.DateTimeField("examiné le", null=True, blank=True)

    class Meta:
        verbose_name = "profil médecin"
        verbose_name_plural = "profils médecins"
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"Dr {self.user.get_full_name() or self.user.email}"

    @property
    def is_verified(self):
        return self.status == self.Status.VERIFIED


def get_role(user):
    """Return 'admin', 'doctor', 'patient' or None for a user."""
    if not user.is_authenticated:
        return None
    if user.is_staff:
        return "admin"
    if hasattr(user, "doctor_profile"):
        return "doctor"
    if hasattr(user, "patient_profile"):
        return "patient"
    return None
