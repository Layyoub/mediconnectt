"""Optional, doctor-only second opinion that cannot lower the rules severity."""

import json
import hashlib
import logging
import re
import threading
from typing import Literal

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone
from pydantic import BaseModel, Field, field_validator

from records.ai_analysis import patient_context
from records.models import Drug, PatientInstructions, PrescriptionCheck, Prescription, PrescriptionItem

from .engine import SEVERITY_ORDER, max_severity
from .matching import normalize_text

logger = logging.getLogger("records")
_DOSE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|ug|g|ml|ui|iu|milligrammes?|microgrammes?|grammes?)\b",
    re.IGNORECASE,
)


class ResolvedDrug(BaseModel):
    typed: str
    dci: str
    drug_class: str
    confidence: float = Field(ge=0, le=1)
    note: str
    based_on: list[str]


class ExtraFinding(BaseModel):
    title: str
    detail: str
    severity: Literal["info", "minor", "moderate", "major", "contraindicated"]
    items: list[str]
    kind: Literal["interaction", "other"] = "other"
    based_on: list[str]


class Alternative(BaseModel):
    instead_of: str
    suggestion: str
    why: str
    based_on: list[str]


class PharmacheckOpinion(BaseModel):
    overall_level: Literal["info", "minor", "moderate", "major", "contraindicated"] = Field(
        description="Niveau global proposé ; ne jamais réduire le niveau des règles."
    )
    resolved_drugs: list[ResolvedDrug] = Field(
        description="Médicaments non reconnus par les règles : typed, dci, drug_class, confidence (0 à 1), note, based_on."
    )
    extra_findings: list[ExtraFinding] = Field(
        description="Alertes supplémentaires title, detail, severity, items, kind, based_on. Une interaction non confirmée ne dépasse pas moderate."
    )
    alternatives: list[Alternative] = Field(
        description="Pistes sans dose : instead_of, suggestion, why, based_on."
    )
    pharmacist_comment: str = Field(description="Commentaire bref en français, en 2 à 4 phrases.")

    @field_validator("resolved_drugs", "extra_findings", "alternatives")
    @classmethod
    def reject_doses_in_items(cls, values):
        data = [value.model_dump() if isinstance(value, BaseModel) else value for value in values]
        if _DOSE.search(json.dumps(data, ensure_ascii=False)):
            raise ValueError("L'avis ne doit proposer ni recopier de dose.")
        return values

    @field_validator("pharmacist_comment")
    @classmethod
    def reject_doses_in_comment(cls, value):
        if _DOSE.search(value):
            raise ValueError("L'avis ne doit proposer ni recopier de dose.")
        return value


class PatientInstructionDraft(BaseModel):
    text_fr: str = Field(description="Consignes simples en français.")
    text_ar: str = Field(description="Consignes simples en arabe standard.")
    included_drugs: list[str] = Field(description="Noms exacts des seuls médicaments de l'ordonnance.")


SYSTEM_PROMPT = """Tu es « Pharmacien IA », une aide à la seconde lecture réservée au médecin.
Appelle obligatoirement les outils `resultats_regles`, `contexte_patient` et `ordonnance_en_cours`.
Retourne uniquement la structure demandée, en français. Ne mentionne aucun nom, e-mail ou identifiant.
Ne pose pas de diagnostic, n'ajoute pas ni ne modifies les résultats des règles, et ne proposes/calcules/recopies aucune dose.
Le niveau global ne peut jamais être inférieur au niveau maximal des règles.
Les médicaments non reconnus peuvent être proposés comme résolus seulement si la correspondance est plausible ; indique une confiance entre 0 et 1 et une note.
Les interactions nouvelles sont des pistes à vérifier, non confirmées par les règles, et ne dépassent jamais le niveau modéré.
Pour chaque résultat indique ce qui le motive dans `based_on` (par exemple traitements en cours, poids, allergies ou ordonnance en cours).
Propose des alternatives non posologiques à discuter avec le médecin uniquement pour les risques importants.
"""


def apply_opinion(rules_level, opinion):
    """Keep all rules intact; AI may add findings and only raise the effective severity."""
    data = opinion.model_dump() if isinstance(opinion, BaseModel) else dict(opinion)
    if rules_level not in SEVERITY_ORDER or data.get("overall_level") not in SEVERITY_ORDER:
        raise ValueError("Niveau de gravité invalide dans l'avis Pharma-Check.")
    unresolved = {
        normalize_text(name)
        for finding in data.get("_rule_findings", [])
        if finding.get("kind") == "unresolved"
        for name in finding.get("medications", [])
    }
    data.pop("_rule_findings", None)
    data["resolved_drugs"] = [
        item for item in data.get("resolved_drugs", [])
        if normalize_text(item["typed"]) in unresolved
    ]
    for item in data.get("resolved_drugs", []):
        item["based_on"] = item.get("based_on") or ["ordonnance en cours"]
    for item in data.get("extra_findings", []):
        if item.get("kind") == "interaction" and SEVERITY_ORDER.index(item["severity"]) > SEVERITY_ORDER.index("moderate"):
            item["severity"] = "moderate"
        item["based_on"] = item.get("based_on") or ["ordonnance en cours"]
    for item in data.get("alternatives", []):
        item["based_on"] = item.get("based_on") or ["résultats des règles et contexte clinique"]
    final_level = max(
        [rules_level, data["overall_level"]]
        + [item["severity"] for item in data.get("extra_findings", [])],
        key=SEVERITY_ORDER.index,
    )
    data["final_level"] = final_level
    return data


def patient_context_data(record):
    context = patient_context(record)
    prescriptions = Prescription.objects.filter(
        record__patient=record.patient, valid_until__gte=timezone.localdate()
    ).order_by("-created_at")
    context["ordonnances_valides"] = list(
        PrescriptionItem.objects.filter(prescription__in=prescriptions).values(
            "drug_name", "dosage", "frequency", "duration"
        )
    )
    return context


def cache_key_for(record, items, findings, confirmed_resolutions=()):
    content = {
        "items": items,
        "context": patient_context_data(record),
        "rules": findings,
        "confirmed_resolutions": list(confirmed_resolutions),
    }
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_agent(check):
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_openai import ChatOpenAI

    rules = {
        "niveau_maximal": max_severity(check.findings),
        "alertes": check.findings,
    }
    context = patient_context_data(check.record)
    prescription = check.checked_items

    @tool
    def resultats_regles() -> str:
        """Renvoie les alertes calculées uniquement par les règles locales."""
        return json.dumps(rules, ensure_ascii=False)

    @tool
    def contexte_patient() -> str:
        """Renvoie le contexte clinique, les traitements et les ordonnances valides sans identifiant."""
        return json.dumps(context, ensure_ascii=False)

    @tool
    def ordonnance_en_cours() -> str:
        """Renvoie les lignes saisies dans l'ordonnance en cours, sans nom ni e-mail."""
        return json.dumps(prescription, ensure_ascii=False)

    model = ChatOpenAI(
        model=settings.PHARMACHECK_AI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
        timeout=settings.PHARMACHECK_AI_TIMEOUT,
        max_retries=1,
    )
    agent = create_agent(
        model,
        tools=[resultats_regles, contexte_patient, ordonnance_en_cours],
        system_prompt=SYSTEM_PROMPT,
        response_format=PharmacheckOpinion,
    )
    state = agent.invoke({
        "messages": [{
            "role": "user",
            "content": "Analyse la prescription à partir des trois outils et rédige un avis prudent, sans proposer de dose.",
        }]
    })
    return state["structured_response"]


def analyse(check):
    """Fill an AI status and retain the rule-only result on any provider/schema failure."""
    try:
        report = run_agent(check)
        report_data = report.model_dump()
        report_data["_rule_findings"] = check.findings
        check.ai_opinion = apply_opinion(max_severity(check.findings), report_data)
        check.max_severity = check.ai_opinion["final_level"]
        check.ai_model = settings.PHARMACHECK_AI_MODEL
        check.ai_status = PrescriptionCheck.AIStatus.DONE
        check.ai_error = ""
    except Exception as exc:
        logger.warning("Pharmacheck AI check %s failed: %s", check.pk, exc)
        check.ai_status = PrescriptionCheck.AIStatus.ERROR
        check.ai_error = f"{exc.__class__.__name__}: {exc}"[:300]
    check.save(update_fields=["ai_status", "ai_opinion", "ai_error", "ai_model", "max_severity"])
    return check


def _run_in_thread(pk):
    close_old_connections()
    try:
        check = PrescriptionCheck.objects.select_related(
            "record__patient__patient_profile"
        ).get(pk=pk)
        analyse(check)
    finally:
        connection.close()


def start_analysis(check):
    if not settings.PHARMACHECK_AI_ENABLED:
        raise RuntimeError("Avis IA désactivé : clé OpenAI absente ou analyse désactivée.")
    check.ai_status = PrescriptionCheck.AIStatus.RUNNING
    check.ai_error = ""
    if not check.ai_cache_key:
        check.ai_cache_key = cache_key_for(
            check.record, check.checked_items, check.findings, check.confirmed_resolutions
        )
    check.save(update_fields=["ai_status", "ai_error", "ai_cache_key"])
    if settings.PHARMACHECK_AI_ASYNC:
        threading.Thread(target=_run_in_thread, args=(check.pk,), daemon=True).start()
        return check
    return analyse(check)


def mark_stale(check):
    if check.ai_status == PrescriptionCheck.AIStatus.RUNNING and (
        timezone.now() - check.created_at >= PrescriptionCheck.STALE_AFTER
    ):
        check.ai_status = PrescriptionCheck.AIStatus.ERROR
        check.ai_error = "L'analyse a dépassé le délai maximal de cinq minutes."
        check.save(update_fields=["ai_status", "ai_error"])
    return check


def run_patient_instruction_agent(instructions):
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_openai import ChatOpenAI

    saved_items = list(
        PrescriptionItem.objects.filter(prescription=instructions.prescription)
        .values("drug_name", "dosage", "frequency", "duration", "instructions", "drug__dci")
    )

    @tool
    def ordonnance_en_cours() -> str:
        """Renvoie exclusivement les médicaments et posologies déjà enregistrés."""
        return json.dumps(saved_items, ensure_ascii=False)

    model = ChatOpenAI(
        model=settings.PHARMACHECK_AI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
        timeout=settings.PHARMACHECK_AI_TIMEOUT,
        max_retries=1,
    )
    agent = create_agent(
        model,
        tools=[ordonnance_en_cours],
        system_prompt=(
            "Tu rédiges un brouillon de consignes patient en français simple et en arabe standard. "
            "Appelle l'outil ordonnance_en_cours. Utilise uniquement les médicaments et horaires exacts "
            "enregistrés, ne crée aucun médicament, aucune dose ou quantité. Explique les précautions "
            "générales, les signes d'alerte et quand contacter un médecin. Ne donne pas de diagnostic."
        ),
        response_format=PatientInstructionDraft,
    )
    state = agent.invoke({
        "messages": [{
            "role": "user",
            "content": "Rédige les consignes uniquement à partir de cette ordonnance sauvegardée.",
        }]
    })
    report = state["structured_response"]
    expected_names = {normalize_text(item["drug_name"]) for item in saved_items}
    included_names = {normalize_text(name) for name in report.included_drugs}
    if included_names != expected_names:
        raise ValueError("Les consignes ne correspondent pas exactement aux médicaments sauvegardés.")
    source_text = json.dumps(saved_items, ensure_ascii=False).lower()
    allowed_doses = {re.sub(r"\s+", "", value) for value in _DOSE.findall(source_text)}
    source_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", source_text))
    allowed_names = set(expected_names)
    allowed_names.update(
        normalize_text(item["drug__dci"]) for item in saved_items if item["drug__dci"]
    )
    output_text = normalize_text(report.text_fr + " " + report.text_ar)
    for name, dci in Drug.objects.values_list("name", "dci"):
        candidate = normalize_text(name)
        active_ingredient = normalize_text(dci)
        for term in (candidate, active_ingredient):
            if len(term) >= 5 and f" {term} " in f" {output_text} " and term not in allowed_names:
                raise ValueError("Les consignes semblent introduire un médicament absent de l'ordonnance.")
    for text in (report.text_fr, report.text_ar):
        found = {re.sub(r"\s+", "", value) for value in _DOSE.findall(text.lower())}
        if not found.issubset(allowed_doses):
            raise ValueError("Les consignes contiennent une dose absente de l'ordonnance.")
        if not set(re.findall(r"\d+(?:[.,]\d+)?", text)).issubset(source_numbers):
            raise ValueError("Les consignes contiennent une quantité absente de l'ordonnance.")
    return report


def analyse_patient_instructions(instructions):
    try:
        report = run_patient_instruction_agent(instructions)
        instructions.text_fr = report.text_fr
        instructions.text_ar = report.text_ar
        instructions.generation_status = PatientInstructions.GenerationStatus.DONE
        instructions.generation_error = ""
    except Exception as exc:
        logger.warning("Patient instructions %s failed: %s", instructions.pk, exc)
        instructions.generation_status = PatientInstructions.GenerationStatus.ERROR
        instructions.generation_error = f"{exc.__class__.__name__}: {exc}"[:300]
    instructions.save(update_fields=[
        "text_fr", "text_ar", "generation_status", "generation_error", "updated_at"
    ])
    return instructions


def _instructions_in_thread(pk):
    close_old_connections()
    try:
        instructions = PatientInstructions.objects.select_related("prescription").get(pk=pk)
        analyse_patient_instructions(instructions)
    finally:
        connection.close()


def start_patient_instructions(prescription):
    instructions, created = PatientInstructions.objects.get_or_create(
        prescription=prescription,
        defaults={"generation_status": PatientInstructions.GenerationStatus.RUNNING},
    )
    if not created and instructions.generation_status != PatientInstructions.GenerationStatus.ERROR:
        return instructions
    instructions.generation_status = PatientInstructions.GenerationStatus.RUNNING
    instructions.generation_error = ""
    if not settings.PHARMACHECK_AI_ENABLED:
        instructions.generation_status = PatientInstructions.GenerationStatus.ERROR
        instructions.generation_error = "Génération IA désactivée ; les consignes ne sont pas publiées."
        instructions.save(update_fields=["generation_status", "generation_error", "updated_at"])
        return instructions
    instructions.save(update_fields=["generation_status", "generation_error", "updated_at"])
    if settings.PHARMACHECK_AI_ASYNC:
        threading.Thread(
            target=_instructions_in_thread, args=(instructions.pk,), daemon=True
        ).start()
        return instructions
    return analyse_patient_instructions(instructions)


def mark_instructions_stale(instructions):
    if (
        instructions.generation_status == PatientInstructions.GenerationStatus.RUNNING
        and timezone.now() - instructions.updated_at >= PrescriptionCheck.STALE_AFTER
    ):
        instructions.generation_status = PatientInstructions.GenerationStatus.ERROR
        instructions.generation_error = "La génération a dépassé le délai maximal de cinq minutes."
        instructions.save(update_fields=["generation_status", "generation_error", "updated_at"])
    return instructions
