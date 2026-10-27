"""LangChain triage agent (OpenAI).

The agent reads the patient's answers, calls tools to get the rule-engine
evaluation and the list of specialties, then returns a structured result
(level, explanation, recommendation, specialty, warning signs).

Safety: the caller (engine.complete_session) never lets the agent's level go
below the level imposed by the red-flag rules.

Privacy: only clinical data is sent (age, complaint, symptoms, temperature,
risk factors), never the patient's name or e-mail.
"""

import json
from typing import Literal

from django.conf import settings
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from core.models import DoctorProfile

from .models import Duration, RiskFactor, Severity
from .symptoms import SYMPTOMS_BY_CODE

SpecialtyCode = Literal[tuple(DoctorProfile.Specialty.values)]


class AITriageOutput(BaseModel):
    """Structured answer the agent must return."""

    level: Literal["low", "moderate", "high", "urgent"] = Field(
        description="Niveau d'urgence : low (faible), moderate (modéré), high (élevé), urgent."
    )
    summary: str = Field(
        description="Analyse personnalisée pour le patient, 2 à 4 phrases, en français, ton rassurant "
                    "et clair. Pas de diagnostic certain : parler d'hypothèses possibles."
    )
    recommendation: str = Field(
        description="Ce que le patient doit faire maintenant et dans quel délai, 1 à 2 phrases."
    )
    reasons: list[str] = Field(
        description="3 à 6 éléments des réponses du patient qui justifient le niveau, phrases courtes.",
        min_length=1, max_length=8,
    )
    warning_signs: list[str] = Field(
        description="2 à 4 signes qui doivent amener à consulter en urgence s'ils apparaissent.",
        max_length=6,
    )
    suggested_specialty: SpecialtyCode = Field(description="Code de la spécialité médicale conseillée.")


SYSTEM_PROMPT = """Tu es l'assistant de triage de MediConnect, une plateforme de téléconsultation.
Ton rôle : évaluer le niveau d'urgence des symptômes d'un patient et l'orienter. Tu ne poses pas de diagnostic.

Méthode obligatoire :
1. Appelle l'outil `evaluer_avec_regles` pour obtenir l'évaluation du moteur de règles médicales
   (score, niveau, signes d'alerte déclenchés).
2. Appelle l'outil `lister_specialites` pour connaître les spécialités disponibles.
3. Analyse l'ensemble (motif, symptômes, intensité, durée, âge, température, facteurs de risque)
   et décide du niveau final.

Règles de sécurité impératives :
- Le niveau final ne doit JAMAIS être inférieur au niveau imposé par un signe d'alerte (`niveau_minimum`).
- En cas de doute entre deux niveaux, choisis le plus élevé.
- Si le niveau est « urgent », la recommandation doit dire d'appeler immédiatement les secours (112 ou numéro local).
- Ne cite jamais une maladie comme certaine ; parle d'« hypothèses » ou de « causes possibles ».
- Ne recommande aucun médicament sur ordonnance ni posologie.
- Réponds uniquement en français, avec des phrases simples, adaptées à un patient.
- Si le motif libre contient des éléments inquiétants non couverts par les symptômes cochés
  (ex. perte de connaissance, convulsions), prends-les en compte et augmente le niveau si besoin.
"""


def describe_session(session):
    """Patient answers as plain text (no identity data)."""
    severity = dict(Severity.choices)
    duration = dict(Duration.choices)
    risks = dict(RiskFactor.choices)
    lines = [
        f"Âge : {session.age} ans" if session.age is not None else "Âge : non renseigné",
        f"Motif décrit par le patient : « {session.chief_complaint} »",
        f"Température : {session.temperature} °C" if session.temperature is not None else "Température : non mesurée",
        "Facteurs de risque : " + (", ".join(risks[c] for c in session.risk_factors if c in risks) or "aucun"),
        "Symptômes déclarés :",
    ]
    for entry in session.symptoms.all():
        info = SYMPTOMS_BY_CODE[entry.symptom]
        lines.append(f"- {info.label} : intensité {severity[entry.severity].lower()}, depuis {duration[entry.duration].lower()}")
    return "\n".join(lines)


def build_tools(rule_outcome):
    """Tools are built per request so they can see this session's rule evaluation."""

    @tool
    def evaluer_avec_regles() -> str:
        """Renvoie l'évaluation du moteur de règles médicales MediConnect pour ce patient :
        score, niveau calculé, signes d'alerte déclenchés et niveau minimum imposé."""
        floor = "low"
        order = ["low", "moderate", "high", "urgent"]
        for rule in rule_outcome.red_flags:
            if order.index(rule.level) > order.index(floor):
                floor = rule.level
        return json.dumps({
            "score": rule_outcome.score,
            "niveau_regles": rule_outcome.level,
            "niveau_minimum": floor,
            "signes_alerte": [{"nom": r.name, "niveau": r.level, "message": r.message} for r in rule_outcome.red_flags],
            "elements": [r["label"] for r in rule_outcome.reasons],
            "specialite_regles": rule_outcome.suggested_specialty,
        }, ensure_ascii=False)

    @tool
    def lister_specialites() -> str:
        """Renvoie la liste des spécialités médicales disponibles sur MediConnect (code : libellé)."""
        return json.dumps(dict(DoctorProfile.Specialty.choices), ensure_ascii=False)

    return [evaluer_avec_regles, lister_specialites]


def run_agent(session, rule_outcome):
    """Run the LangChain agent and return an AITriageOutput. Raises on any failure."""
    model = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.2,
        timeout=settings.TRIAGE_AI_TIMEOUT,
        max_retries=1,
    )
    agent = create_agent(
        model,
        tools=build_tools(rule_outcome),
        system_prompt=SYSTEM_PROMPT,
        response_format=AITriageOutput,
    )
    state = agent.invoke({"messages": [{"role": "user", "content": describe_session(session)}]})
    return state["structured_response"]
