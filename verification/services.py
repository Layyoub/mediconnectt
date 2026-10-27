"""Run a doctor verification (rules + AI agent), synchronously or in a background thread."""

import logging
import threading

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

from .models import DoctorVerification
from .rules import document_contents, risk_from_checks, run_rules

logger = logging.getLogger("verification")
RISK_ORDER = ["low", "medium", "high"]


def _max_risk(*risks):
    return max(risks, key=RISK_ORDER.index)


MODEL_CHECKS = {"diploma_model_match", "license_model_match"}


def ai_floor(ai_checks):
    """Minimum risk implied by the agent's own findings (it cannot say "low" next to a failure)."""
    if any(c["status"] == "fail" for c in ai_checks):
        return "high"
    warnings = [c for c in ai_checks if c["status"] == "warning"]
    if len(warnings) >= 2 or any(c["key"] in MODEL_CHECKS for c in warnings):
        return "medium"
    return "low"


def _rules_summary(checks):
    fails = [c["label"] for c in checks if c["status"] == "fail"]
    warnings = [c["label"] for c in checks if c["status"] == "warning"]
    parts = []
    if fails:
        parts.append("Problèmes détectés : " + ", ".join(fails).lower() + ".")
    if warnings:
        parts.append("Points à vérifier : " + ", ".join(warnings).lower() + ".")
    if not parts:
        parts.append("Les contrôles automatiques n'ont rien relevé.")
    parts.append("Le contenu des documents n'a pas été analysé par l'IA : ouvrez-les et vérifiez-les vous-même.")
    return " ".join(parts)


def analyse(verification):
    """Fill a RUNNING verification. Never raises: errors end up in the record."""
    doctor = verification.doctor
    try:
        contents = document_contents(doctor)
        checks = run_rules(doctor, contents)
        rules_risk = risk_from_checks(checks)

        if settings.VERIFICATION_AI_ENABLED:
            try:
                from .agent import run_agent, to_checks  # LangChain is slow to import

                output = run_agent(doctor, contents, checks)
                ai_checks = to_checks(output)
                checks += ai_checks
                verification.ai_used = True
                verification.ai_model = settings.VERIFICATION_MODEL
                verification.risk = _max_risk(output.risk, rules_risk, ai_floor(ai_checks))
                verification.recommendation = output.recommendation
                if verification.risk != "low" and output.recommendation == "approve":
                    verification.recommendation = "review"
                verification.summary = output.summary
                if verification.risk != output.risk:
                    verification.summary += (
                        f" Niveau relevé à « {verification.get_risk_display().lower()} » par MediConnect : "
                        "un ou plusieurs contrôles sont en échec ou à vérifier."
                    )
            except Exception as exc:  # network, quota, invalid output…
                logger.warning("Verification AI failed for doctor %s: %s", doctor.pk, exc)
                verification.error = f"Analyse IA indisponible : {exc.__class__.__name__}"[:300]

        if not verification.ai_used:
            # Without the AI, the documents' content is unchecked: never "low".
            verification.risk = _max_risk(rules_risk, "medium")
            verification.recommendation = "reject" if rules_risk == "high" else "review"
            verification.summary = _rules_summary(checks)

        verification.checks = checks
        verification.status = DoctorVerification.Status.DONE
    except Exception as exc:
        logger.exception("Verification failed for doctor %s", doctor.pk)
        verification.status = DoctorVerification.Status.ERROR
        verification.error = f"{exc.__class__.__name__}: {exc}"[:300]
    verification.finished_at = timezone.now()
    verification.save()
    return verification


def _run_in_thread(pk):
    close_old_connections()
    try:
        analyse(DoctorVerification.objects.select_related("doctor__user").get(pk=pk))
    finally:
        connection.close()


def start_verification(doctor, by=None):
    """Create the analysis and run it (in the background when the AI is used)."""
    verification = DoctorVerification.objects.create(doctor=doctor, triggered_by=by)
    if settings.VERIFICATION_AI_ENABLED and settings.VERIFICATION_ASYNC:
        threading.Thread(target=_run_in_thread, args=(verification.pk,), daemon=True).start()
        return verification
    return analyse(verification)
