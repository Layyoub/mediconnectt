from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import DoctorProfile

from .symptoms import SYMPTOM_CHOICES, SYMPTOMS_BY_CODE


class TriageLevel(models.TextChoices):
    LOW = "low", "Faible"
    MODERATE = "moderate", "Modéré"
    HIGH = "high", "Élevé"
    URGENT = "urgent", "Urgent"


LEVEL_RANK = {TriageLevel.LOW: 0, TriageLevel.MODERATE: 1, TriageLevel.HIGH: 2, TriageLevel.URGENT: 3}


class Severity(models.IntegerChoices):
    MILD = 1, "Léger"
    MODERATE = 2, "Modéré"
    SEVERE = 3, "Sévère"


class Duration(models.TextChoices):
    LESS_24H = "lt24h", "Moins de 24 h"
    DAYS_1_3 = "1-3d", "1 à 3 jours"
    DAYS_4_7 = "4-7d", "4 à 7 jours"
    MORE_WEEK = "gt7d", "Plus d'une semaine"


class RiskFactor(models.TextChoices):
    CHRONIC = "chronic", "Maladie chronique (diabète, asthme, maladie cardiaque…)"
    PREGNANCY = "pregnancy", "Grossesse en cours"
    IMMUNO = "immuno", "Immunité affaiblie (traitement, maladie)"
    SMOKER = "smoker", "Tabagisme"


class TriageSession(models.Model):
    """One symptom assessment filled in by a patient."""

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "En cours"
        COMPLETED = "completed", "Terminée"

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="triage_sessions", verbose_name="patient"
    )
    status = models.CharField("statut", max_length=12, choices=Status.choices, default=Status.IN_PROGRESS)
    chief_complaint = models.TextField("motif principal")
    temperature = models.DecimalField(
        "température (°C)", max_digits=3, decimal_places=1, null=True, blank=True
    )
    risk_factors = models.JSONField("facteurs de risque", default=list, blank=True)
    age = models.PositiveSmallIntegerField("âge au moment de l'évaluation", null=True, blank=True)
    created_at = models.DateTimeField("commencée le", auto_now_add=True)
    completed_at = models.DateTimeField("terminée le", null=True, blank=True)

    class Meta:
        verbose_name = "session de triage"
        verbose_name_plural = "sessions de triage"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Triage #{self.pk} · {self.patient.get_full_name() or self.patient.email}"

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    def risk_factor_labels(self):
        labels = dict(RiskFactor.choices)
        return [labels[code] for code in self.risk_factors if code in labels]

    @staticmethod
    def age_from_birth_date(birth_date):
        if not birth_date:
            return None
        today = date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


class SymptomEntry(models.Model):
    """One symptom reported during a session, with its severity and duration."""

    session = models.ForeignKey(TriageSession, on_delete=models.CASCADE, related_name="symptoms")
    symptom = models.CharField("symptôme", max_length=30, choices=SYMPTOM_CHOICES)
    severity = models.PositiveSmallIntegerField("intensité", choices=Severity.choices)
    duration = models.CharField("depuis", max_length=5, choices=Duration.choices)

    class Meta:
        verbose_name = "symptôme déclaré"
        verbose_name_plural = "symptômes déclarés"
        constraints = [models.UniqueConstraint(fields=["session", "symptom"], name="unique_symptom_per_session")]

    def __str__(self):
        return f"{self.get_symptom_display()} ({self.get_severity_display().lower()})"

    @property
    def info(self):
        return SYMPTOMS_BY_CODE.get(self.symptom)


class RedFlagRule(models.Model):
    """A safety rule: when it matches, the triage level is raised to at least `level`.

    A rule matches when ALL its filled conditions are true:
    - `symptom` is reported with at least `min_severity`
    - `combined_symptom` is also reported (any severity)
    - the temperature is at least `min_temperature`
    """

    name = models.CharField("nom", max_length=120)
    symptom = models.CharField("symptôme", max_length=30, choices=SYMPTOM_CHOICES, blank=True)
    min_severity = models.PositiveSmallIntegerField(
        "intensité minimale", choices=Severity.choices, default=Severity.MILD
    )
    combined_symptom = models.CharField(
        "associé au symptôme", max_length=30, choices=SYMPTOM_CHOICES, blank=True
    )
    min_temperature = models.DecimalField(
        "température minimale (°C)", max_digits=3, decimal_places=1, null=True, blank=True
    )
    level = models.CharField(
        "niveau imposé",
        max_length=10,
        choices=[(TriageLevel.HIGH, TriageLevel.HIGH.label), (TriageLevel.URGENT, TriageLevel.URGENT.label)],
        default=TriageLevel.URGENT,
    )
    message = models.TextField("message affiché au patient")
    is_active = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "règle d'alerte (red flag)"
        verbose_name_plural = "règles d'alerte (red flags)"
        ordering = ["-level", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        if not self.symptom and self.min_temperature is None:
            raise ValidationError("Une règle doit porter sur un symptôme ou sur une température minimale.")

    def matches(self, entries_by_code, temperature):
        if self.symptom:
            entry = entries_by_code.get(self.symptom)
            if entry is None or entry.severity < self.min_severity:
                return False
        if self.combined_symptom and self.combined_symptom not in entries_by_code:
            return False
        if self.min_temperature is not None and (temperature is None or temperature < self.min_temperature):
            return False
        return True


class TriageResult(models.Model):
    """Outcome of a completed session, computed by triage.engine."""

    session = models.OneToOneField(TriageSession, on_delete=models.CASCADE, related_name="result")
    level = models.CharField("niveau d'urgence", max_length=10, choices=TriageLevel.choices)
    score = models.DecimalField("score", max_digits=5, decimal_places=1)
    recommendation = models.TextField("recommandation")
    reasons = models.JSONField("éléments pris en compte", default=list)
    suggested_specialty = models.CharField(
        "spécialité conseillée", max_length=30, choices=DoctorProfile.Specialty.choices
    )
    red_flags = models.ManyToManyField(RedFlagRule, blank=True, related_name="results", verbose_name="alertes déclenchées")
    summary = models.TextField("analyse de l'assistant", blank=True)
    warning_signs = models.JSONField("signes devant amener à reconsulter", default=list, blank=True)
    engine_version = models.CharField("moteur", max_length=40, default="rules-v1")
    created_at = models.DateTimeField("calculé le", auto_now_add=True)

    class Meta:
        verbose_name = "résultat de triage"
        verbose_name_plural = "résultats de triage"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_level_display()} · {self.session}"

    @property
    def rank(self):
        return LEVEL_RANK[self.level]

    @property
    def by_ai(self):
        return self.engine_version.startswith("langchain")
