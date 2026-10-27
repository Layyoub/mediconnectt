from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import DoctorProfile

STALE_AFTER = timedelta(minutes=5)  # a "running" analysis older than this was interrupted


class DoctorVerification(models.Model):
    """One automatic analysis of a doctor's application (rules + AI agent).

    It only helps the staff decide: the decision itself stays manual.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "En cours"
        DONE = "done", "Terminée"
        ERROR = "error", "Échec"

    class Risk(models.TextChoices):
        LOW = "low", "Risque faible"
        MEDIUM = "medium", "Risque moyen"
        HIGH = "high", "Risque élevé"

    class Recommendation(models.TextChoices):
        APPROVE = "approve", "Dossier cohérent : peut être approuvé après un dernier contrôle"
        REVIEW = "review", "Vérification manuelle approfondie conseillée"
        REJECT = "reject", "Dossier suspect : refus ou demande de nouveaux justificatifs conseillé"

    doctor = models.ForeignKey(
        DoctorProfile, on_delete=models.CASCADE, related_name="verifications", verbose_name="médecin"
    )
    status = models.CharField("état", max_length=10, choices=Status.choices, default=Status.RUNNING)
    risk = models.CharField("niveau de risque", max_length=10, choices=Risk.choices, blank=True)
    recommendation = models.CharField("recommandation", max_length=10, choices=Recommendation.choices, blank=True)
    summary = models.TextField("synthèse", blank=True)
    # [{"key", "label", "status": ok|warning|fail|info|unknown, "detail", "source": rules|ai}]
    checks = models.JSONField("contrôles", default=list, blank=True)
    ai_used = models.BooleanField("analyse IA effectuée", default=False)
    ai_model = models.CharField("modèle", max_length=60, blank=True)
    error = models.CharField("erreur", max_length=300, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="lancée par",
    )
    created_at = models.DateTimeField("lancée le", auto_now_add=True)
    finished_at = models.DateTimeField("terminée le", null=True, blank=True)

    class Meta:
        verbose_name = "analyse de dossier médecin"
        verbose_name_plural = "analyses de dossiers médecins"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Analyse de {self.doctor} · {self.created_at:%d/%m/%Y %H:%M}"

    @property
    def is_running(self):
        return self.status == self.Status.RUNNING and timezone.now() - self.created_at < STALE_AFTER

    @property
    def is_stale(self):
        return self.status == self.Status.RUNNING and not self.is_running

    def checks_from(self, source):
        return [c for c in self.checks if c.get("source") == source]

    @property
    def ai_checks(self):
        return self.checks_from("ai")

    @property
    def rule_checks(self):
        return self.checks_from("rules")

    @property
    def counts(self):
        result = {"ok": 0, "warning": 0, "fail": 0}
        for check in self.checks:
            if check.get("status") in result:
                result[check["status"]] += 1
        return result
