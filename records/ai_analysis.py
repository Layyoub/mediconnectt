"""LangChain agent (OpenAI, vision) that reads a patient's document (blood test, imaging
report…) and writes a report for the doctor.

The agent receives the document (PDF or image) and can call a tool to get the clinical
context (age, sex, allergies, conditions, treatments, reason for the consultation) so it
can point out what matters for this patient. It does not diagnose: it extracts, flags and
suggests points to check. The report is only shown to doctors.

Privacy: the patient's name and e-mail are never written in the request. The document
itself is sent as is, so whatever is printed on it (often the patient's name) goes too:
patients are told so when they share a document.
"""

import base64
import json
import logging
import threading
from typing import Literal

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone
from pydantic import BaseModel, Field

from triage.models import TriageSession

from .forms import MAGIC_BYTES
from .models import DocumentAnalysis

logger = logging.getLogger("records")

MIME_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}


class LabValue(BaseModel):
    name: str = Field(description="Nom du paramètre tel qu'écrit (ex. Hémoglobine, Glycémie à jeun, CRP).")
    value: str = Field(description="Valeur mesurée, telle qu'écrite.")
    unit: str = Field(description="Unité (g/dL, mmol/L…), vide si absente.")
    reference: str = Field(description="Valeurs de référence indiquées sur le document, vide si absentes.")
    flag: Literal["normal", "high", "low", "abnormal", "unknown"] = Field(
        description="Par rapport aux valeurs de référence du document (ou usuelles si absentes) : "
                    "normal, high (au-dessus), low (en dessous), abnormal (qualitatif anormal), unknown."
    )
    comment: str = Field(description="Remarque courte si utile (ex. « légèrement élevé »), sinon vide.")


class DocumentReport(BaseModel):
    readable: bool = Field(description="False si le document est illisible ou n'est pas un document médical.")
    document_type: str = Field(description="Type de document (ex. « Bilan sanguin - NFS, ionogramme », « Compte rendu de radiographie »).")
    exam_date: str = Field(description="Date de l'examen écrite sur le document, vide si absente.")
    values: list[LabValue] = Field(description="Toutes les valeurs chiffrées ou qualitatives lisibles, dans l'ordre du document.")
    abnormal_findings: list[str] = Field(
        description="Anomalies importantes en phrases courtes (valeur + interprétation prudente), les plus importantes d'abord."
    )
    summary: str = Field(
        description="Compte rendu pour le médecin, 3 à 6 phrases, en français médical, en tenant compte du contexte du patient. "
                    "Parle d'hypothèses, jamais de diagnostic certain."
    )
    points_to_check: list[str] = Field(
        description="2 à 5 pistes pour le médecin : questions à poser, examens complémentaires possibles, "
                    "interactions avec les traitements ou allergies du patient."
    )


SYSTEM_PROMPT = """Tu es l'assistant d'analyse de documents médicaux de MediConnect.
Un médecin en téléconsultation te confie un document de son patient (bilan sanguin, analyse d'urine,
compte rendu d'imagerie…). Tu rédiges pour LE MÉDECIN un compte rendu clair et fiable.

Méthode obligatoire :
1. Appelle l'outil `contexte_patient` pour connaître l'âge, le sexe, les antécédents, les traitements,
   les allergies et le motif de la consultation.
2. Lis tout le document et relève chaque valeur avec son unité et ses valeurs de référence.
3. Compare chaque valeur aux valeurs de référence imprimées sur le document (sinon, aux normes usuelles
   adaptées à l'âge et au sexe) et signale celles qui sont hors normes.
4. Rédige la synthèse et les points à vérifier en tenant compte du contexte du patient.

Règles :
- Recopie les valeurs exactement : n'invente aucune valeur, unité ou norme. Si c'est illisible, dis-le.
- Ne pose pas de diagnostic certain : « évoque », « compatible avec », « à confronter à la clinique ».
- Signale clairement une valeur qui pourrait nécessiter une prise en charge rapide.
- Si le document n'est pas médical ou est illisible : readable = false et explique-le dans la synthèse.
- Ne mentionne pas le nom du patient dans ta réponse.
- Réponds en français.
"""


def patient_context(record, appointment=None):
    patient = record.patient
    profile = patient.patient_profile
    age = TriageSession.age_from_birth_date(profile.date_of_birth)
    context = {
        "age": f"{age} ans" if age is not None else "non renseigné",
        "sexe": profile.get_gender_display() or "non renseigné",
        "allergies": record.allergies or "aucune connue",
        "maladies_chroniques": record.chronic_conditions or "aucune connue",
        "traitements_en_cours": record.current_treatments or "aucun connu",
        "antecedents": record.medical_history or "non renseignés",
    }
    if record.weight_kg:
        context["poids"] = f"{record.weight_kg} kg"
    if appointment is not None:
        context["motif_de_la_consultation"] = appointment.reason
    return context


def is_sendable(data, ext):
    """A broken file makes OpenAI reject the whole request."""
    if not data or len(data) < 200 or not any(data.startswith(sig) for sig in MAGIC_BYTES.get(ext, [])):
        return False
    return ext != "pdf" or b"%%EOF" in data


def file_block(document, data, ext):
    encoded = base64.b64encode(data).decode("ascii")
    if ext == "pdf":
        return {"type": "file", "base64": encoded, "mime_type": "application/pdf", "filename": f"document-{document.pk}.pdf"}
    return {"type": "image", "base64": encoded, "mime_type": MIME_TYPES[ext]}


def run_agent(document, data, ext):
    from langchain.agents import create_agent  # slow to import: only when needed
    from langchain.tools import tool
    from langchain_openai import ChatOpenAI

    record = document.record
    context = patient_context(record, document.appointment)

    @tool
    def contexte_patient() -> str:
        """Renvoie le contexte clinique du patient : âge, sexe, allergies, maladies chroniques,
        traitements en cours, antécédents, poids et motif de la consultation."""
        return json.dumps(context, ensure_ascii=False)

    model = ChatOpenAI(
        model=settings.DOCUMENT_AI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
        timeout=settings.DOCUMENT_AI_TIMEOUT,
        max_retries=1,
    )
    agent = create_agent(model, tools=[contexte_patient], system_prompt=SYSTEM_PROMPT, response_format=DocumentReport)
    intro = (
        f"Document partagé par le patient : « {document.title} » ({document.get_kind_display()}"
        + (f", daté du {document.document_date:%d/%m/%Y}" if document.document_date else "")
        + "). Rédige le compte rendu pour le médecin."
    )
    state = agent.invoke({"messages": [{"role": "user", "content": [{"type": "text", "text": intro}, file_block(document, data, ext)]}]})
    return state["structured_response"]


def analyse(analysis):
    """Fill a RUNNING analysis. Never raises: errors are stored in the record."""
    document = analysis.document
    try:
        ext = document.file.name.rsplit(".", 1)[-1].lower()
        with document.file.open("rb") as handle:
            data = handle.read()
        if not settings.DOCUMENT_AI_ENABLED:
            raise RuntimeError("analyse IA désactivée (clé OpenAI absente)")
        if not is_sendable(data, ext):
            analysis.readable = False
            analysis.summary = "Le fichier est vide, corrompu ou d'un format non pris en charge : il n'a pas pu être analysé."
        else:
            report = run_agent(document, data, ext)
            analysis.readable = report.readable
            analysis.document_type = report.document_type[:150]
            analysis.exam_date = report.exam_date[:40]
            analysis.values = [v.model_dump() for v in report.values]
            analysis.abnormal_findings = report.abnormal_findings
            analysis.points_to_check = report.points_to_check
            analysis.summary = report.summary
            analysis.ai_model = settings.DOCUMENT_AI_MODEL
        analysis.status = DocumentAnalysis.Status.DONE
    except Exception as exc:
        logger.warning("Document analysis %s failed: %s", analysis.pk, exc)
        analysis.status = DocumentAnalysis.Status.ERROR
        analysis.error = f"{exc.__class__.__name__}: {exc}"[:300]
    analysis.finished_at = timezone.now()
    analysis.save()
    return analysis


def _run_in_thread(pk):
    close_old_connections()
    try:
        analyse(DocumentAnalysis.objects.select_related("document__record__patient__patient_profile").get(pk=pk))
    finally:
        connection.close()


def start_analysis(document, by=None):
    """Create the analysis and run it in the background (synchronously in tests)."""
    analysis = DocumentAnalysis.objects.create(document=document, requested_by=by)
    if settings.DOCUMENT_AI_ENABLED and settings.DOCUMENT_AI_ASYNC:
        threading.Thread(target=_run_in_thread, args=(analysis.pk,), daemon=True).start()
        return analysis
    return analyse(analysis)


def latest_analysis(document):
    return document.analyses.first()


