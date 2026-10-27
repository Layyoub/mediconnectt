"""Rule-based triage engine.

Turns a TriageSession (symptoms + context) into a level, a score, a
recommendation and the reasons behind it. It is deterministic and has no
external dependency. When TRIAGE_AI_ENABLED is on, complete_session() also
asks the LangChain agent (ai_agent.py) for a personalised analysis, then
keeps the red-flag level as a minimum. If the agent fails, the rule result
is used as is.

How the level is decided
1. Each symptom adds  weight × severity factor × duration factor.
2. Temperature, age and risk factors add fixed points.
3. The total score gives a base level (see SCORE_THRESHOLDS).
4. Any active RedFlagRule that matches raises the level to at least its own
   level. Red flags can only raise the level, never lower it.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LEVEL_RANK, Duration, RedFlagRule, RiskFactor, Severity, TriageLevel, TriageResult
from .symptoms import SYMPTOMS_BY_CODE

ENGINE_VERSION = "rules-v1"
logger = logging.getLogger("triage")

SEVERITY_FACTOR = {Severity.MILD: 1.0, Severity.MODERATE: 1.5, Severity.SEVERE: 2.2}
DURATION_FACTOR = {
    Duration.LESS_24H: 1.0,
    Duration.DAYS_1_3: 1.1,
    Duration.DAYS_4_7: 1.2,
    Duration.MORE_WEEK: 1.3,
}
RISK_POINTS = {
    RiskFactor.CHRONIC: 3,
    RiskFactor.PREGNANCY: 3,
    RiskFactor.IMMUNO: 4,
    RiskFactor.SMOKER: 1,
}
# Upper bounds (exclusive) of each level; anything above is URGENT.
SCORE_THRESHOLDS = [
    (6, TriageLevel.LOW),
    (14, TriageLevel.MODERATE),
    (22, TriageLevel.HIGH),
]

RECOMMENDATIONS = {
    TriageLevel.LOW: (
        "Vos symptômes semblent bénins. Reposez-vous, hydratez-vous et surveillez leur évolution. "
        "Consultez un médecin s'ils persistent au-delà de quelques jours ou s'aggravent."
    ),
    TriageLevel.MODERATE: (
        "Une consultation médicale est recommandée dans les 24 à 48 heures. "
        "Une téléconsultation est adaptée à votre situation."
    ),
    TriageLevel.HIGH: (
        "Consultez un médecin aujourd'hui. Si une téléconsultation n'est pas possible rapidement, "
        "rendez-vous dans un centre de soins ou chez votre médecin traitant."
    ),
    TriageLevel.URGENT: (
        "Appelez immédiatement les services d'urgence ou rendez-vous aux urgences les plus proches. "
        "La téléconsultation n'est pas adaptée à cette situation."
    ),
}


@dataclass
class TriageOutcome:
    level: str
    score: float
    recommendation: str
    suggested_specialty: str
    reasons: list = field(default_factory=list)
    red_flags: list = field(default_factory=list)
    summary: str = ""
    warning_signs: list = field(default_factory=list)
    engine_version: str = ENGINE_VERSION


def _level_from_score(score):
    for upper, level in SCORE_THRESHOLDS:
        if score < upper:
            return level
    return TriageLevel.URGENT


def evaluate(session):
    """Compute the outcome of a session without saving anything."""
    entries = list(session.symptoms.all())
    entries_by_code = {e.symptom: e for e in entries}
    reasons = []
    score = 0.0
    top_symptom, top_points = None, -1.0

    # 1. Symptoms
    for entry in entries:
        info = SYMPTOMS_BY_CODE[entry.symptom]
        points = info.weight * SEVERITY_FACTOR[entry.severity] * DURATION_FACTOR[entry.duration]
        score += points
        reasons.append({
            "label": f"{info.label} — {entry.get_severity_display().lower()}, {entry.get_duration_display().lower()}",
            "points": round(points, 1),
        })
        if points > top_points:
            top_symptom, top_points = info, points

    # 2. Context: temperature, age, risk factors
    temperature = session.temperature
    if temperature is not None:
        temp_label = str(temperature).replace(".", ",")
        if temperature >= Decimal("39.0"):
            score += 3
            reasons.append({"label": f"Fièvre élevée ({temp_label} °C)", "points": 3})
        elif temperature >= Decimal("38.0"):
            score += 1
            reasons.append({"label": f"Fièvre ({temp_label} °C)", "points": 1})

    if session.age is not None and (session.age >= 65 or session.age < 2):
        score += 3
        reasons.append({"label": f"Âge ({session.age} ans) : patient plus vulnérable", "points": 3})

    risk_labels = dict(RiskFactor.choices)
    for code in session.risk_factors:
        if code in RISK_POINTS:
            score += RISK_POINTS[code]
            reasons.append({"label": risk_labels[code], "points": RISK_POINTS[code]})

    level = _level_from_score(score)

    # 3. Red flags can only raise the level
    triggered = [
        rule for rule in RedFlagRule.objects.filter(is_active=True)
        if rule.matches(entries_by_code, temperature)
    ]
    for rule in triggered:
        if LEVEL_RANK[rule.level] > LEVEL_RANK[level]:
            level = TriageLevel(rule.level)

    # 4. Orientation
    specialty = top_symptom.specialty if top_symptom else "general"
    if session.age is not None and session.age < 15 and specialty == "general":
        specialty = "pediatrics"

    reasons.sort(key=lambda r: r["points"], reverse=True)
    return TriageOutcome(
        level=level,
        score=round(score, 1),
        recommendation=RECOMMENDATIONS[level],
        suggested_specialty=specialty,
        reasons=reasons,
        red_flags=triggered,
    )


def red_flag_floor(red_flags):
    """Highest level imposed by the triggered red flags (LOW if none)."""
    floor = TriageLevel.LOW
    for rule in red_flags:
        if LEVEL_RANK[rule.level] > LEVEL_RANK[floor]:
            floor = TriageLevel(rule.level)
    return floor


def apply_ai(outcome, ai):
    """Merge the agent's answer into the rule outcome, keeping the red-flag safety floor."""
    floor = red_flag_floor(outcome.red_flags)
    level = TriageLevel(ai.level)
    raised = LEVEL_RANK[floor] > LEVEL_RANK[level]
    if raised:
        logger.warning("AI level %s was below red-flag floor %s; raised.", level, floor)
        level = floor
    outcome.level = level
    # For "urgent", or when we had to override the agent, use the fixed safety wording.
    outcome.recommendation = RECOMMENDATIONS[level] if raised or level == TriageLevel.URGENT else ai.recommendation
    outcome.reasons = [{"label": label, "points": None} for label in ai.reasons]
    outcome.suggested_specialty = ai.suggested_specialty
    outcome.summary = ai.summary
    outcome.warning_signs = list(ai.warning_signs)
    outcome.engine_version = f"langchain:{settings.OPENAI_MODEL}"[:40]
    return outcome


def complete_session(session):
    """Evaluate the session (AI agent if enabled, rules otherwise), store the result."""
    outcome = evaluate(session)
    if settings.TRIAGE_AI_ENABLED:
        try:
            from .ai_agent import run_agent  # imported lazily: LangChain is slow to load

            outcome = apply_ai(outcome, run_agent(session, outcome))
        except Exception:
            # Network error, timeout, invalid key, bad output… the rule result is still valid.
            logger.exception("Triage AI agent failed for session %s; using rule engine.", session.pk)
    return _save(session, outcome)


@transaction.atomic
def _save(session, outcome):
    result, _ = TriageResult.objects.update_or_create(
        session=session,
        defaults={
            "level": outcome.level,
            "score": Decimal(str(outcome.score)),
            "recommendation": outcome.recommendation,
            "reasons": outcome.reasons,
            "suggested_specialty": outcome.suggested_specialty,
            "summary": outcome.summary,
            "warning_signs": outcome.warning_signs,
            "engine_version": outcome.engine_version,
        },
    )
    result.red_flags.set(outcome.red_flags)
    session.status = session.Status.COMPLETED
    session.completed_at = timezone.now()
    session.save(update_fields=["status", "completed_at"])
    return result
