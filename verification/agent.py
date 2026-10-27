"""LangChain agent (OpenAI, vision) that reads a doctor's credential documents.

The agent receives the information declared at signup and the two documents (diploma, Ordre attestation)
(PDF or image), compares them with the reference models in references/, calls a tool to get the server-side consistency checks, then
returns a structured assessment. It only assists the staff: it never approves
or rejects an account.

Privacy: the two documents, name, specialty, licence number, city and declared
experience are sent to OpenAI (the doctor is told so at signup). The e-mail
and phone number are never sent: they are only checked on the server.
"""

import base64
import json
from pathlib import Path
from typing import Literal

from django.conf import settings
from django.utils import timezone
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .rules import MIME_TYPES, SIGNATURES

Status = Literal["ok", "warning", "fail", "unknown"]


class Finding(BaseModel):
    status: Status = Field(description="ok, warning (doute), fail (problème net) ou unknown (impossible à vérifier).")
    detail: str = Field(description="Explication courte et factuelle en français (1 à 2 phrases).")


class AIVerificationOutput(BaseModel):
    """Structured answer the agent must return."""

    diploma_document: Finding = Field(description="Le document 'diplôme' est-il bien un diplôme de docteur en médecine (ou équivalent) ?")
    license_document: Finding = Field(description="Le document 'attestation' est-il bien une attestation d'inscription à un Ordre des médecins ?")
    diploma_model_match: Finding = Field(description="Le diplôme suit-il le même modèle que le MODÈLE DE RÉFÉRENCE du diplôme "
                                                     "(titre, mise en page, encadrement, emblème, mentions) ? Le contenu personnel peut différer.")
    license_model_match: Finding = Field(description="L'attestation est-elle sur le même papier officiel que le MODÈLE DE RÉFÉRENCE de l'Ordre "
                                                     "(en-tête bilingue « Ordre des Médecins - Conseil National », logo, pied de page, signature/cachet) ?")
    name_match: Finding = Field(description="Le nom déclaré correspond-il au nom figurant sur les documents ?")
    license_number_match: Finding = Field(description="Le numéro d'inscription déclaré figure-t-il sur l'attestation ?")
    specialty_match: Finding = Field(description="La spécialité déclarée est-elle cohérente avec les documents ?")
    dates_coherence: Finding = Field(description="Les dates (obtention du diplôme, inscription à l'Ordre) sont-elles cohérentes avec l'expérience déclarée ?")
    tampering: Finding = Field(description="Y a-t-il des signes de falsification (zones retouchées, polices incohérentes, montage, modèle générique) ?")
    risk: Literal["low", "medium", "high"] = Field(description="Niveau de risque global que le dossier soit faux ou incorrect.")
    recommendation: Literal["approve", "review", "reject"] = Field(
        description="approve : cohérent ; review : vérification humaine approfondie ; reject : dossier manifestement non conforme."
    )
    summary: str = Field(description="Synthèse pour l'administrateur, 2 à 4 phrases, en français.")


AI_LABELS = {
    "diploma_document": "Diplôme de médecine",
    "license_document": "Attestation d'inscription à l'Ordre",
    "diploma_model_match": "Diplôme conforme au modèle",
    "license_model_match": "Attestation conforme au modèle de l'Ordre",
    "name_match": "Nom sur les documents",
    "license_number_match": "N° d'inscription sur l'attestation",
    "specialty_match": "Spécialité",
    "dates_coherence": "Cohérence des dates",
    "tampering": "Signes de falsification",
}


SYSTEM_PROMPT = """Tu es l'agent de vérification des médecins de MediConnect, une plateforme de téléconsultation.
Un médecin vient de s'inscrire. Tu aides l'équipe administrative à juger si son dossier est authentique et cohérent.
Tu ne décides pas : l'administrateur décide. Ton analyse doit être prudente, factuelle et vérifiable.

Méthode obligatoire :
1. Appelle l'outil `controles_automatiques` pour connaître les contrôles déjà faits par le serveur
   (fichiers valides, fichiers déjà utilisés par un autre compte, format du numéro, etc.).
2. Regarde les deux MODÈLES DE RÉFÉRENCE (diplôme et document officiel de l'Ordre des médecins) :
   ce sont des exemples authentiques fournis par MediConnect. Ils montrent la forme attendue,
   pas le contenu : le nom, le numéro, les dates et le texte du médecin sont différents.
3. Examine les deux documents du médecin : type de document, ressemblance avec le modèle
   (titre, en-tête, logos, mise en page, cachet/signature), nom, numéro, spécialité, dates, signes de retouche.
4. Compare avec les informations déclarées par le médecin.
5. Remplis chaque point de contrôle et donne un niveau de risque global.

Règles :
- N'invente rien : si une information n'est pas lisible ou absente, utilise « unknown » et dis-le.
- Un document illisible, vide, sans rapport (photo quelconque, capture d'écran, page blanche) = « fail ».
- Un nom ou un numéro d'inscription différent de celui déclaré = « fail ».
- Conformité au modèle : compare ce que tu VOIS, pas ce que dit le texte. Un document fait uniquement de
  texte tapé (sans logo, sans emblème, sans encadrement, sans en-tête graphique, sans cachet ni signature visibles)
  ne suit PAS le modèle = « fail », même si le texte prétend venir d'une faculté ou de l'Ordre.
  « ok » seulement si les éléments visuels du modèle sont présents ; de petites différences = « warning ».
- Ne décris jamais un cachet, un logo ou une signature que tu ne vois pas.
- Le modèle de l'Ordre peut être un autre type de courrier (communiqué…) : compare l'en-tête, le logo,
  le pied de page et la signature, pas le texte.
- Si un contrôle automatique est en échec (« fail »), le risque global est au moins « high ».
- Tu ne peux pas consulter le registre officiel de l'Ordre : rappelle dans la synthèse que l'inscription
  doit être confirmée auprès de l'Ordre si le risque n'est pas faible.
- Réponds uniquement en français, en phrases courtes.
"""


def describe_declared(doctor):
    today = timezone.localdate()
    return "\n".join([
        f"Date du jour : {today:%d/%m/%Y}",
        "Informations déclarées par le médecin à l'inscription :",
        f"- Nom : {doctor.user.get_full_name()}",
        f"- Spécialité : {doctor.get_specialty_display()}",
        f"- N° d'inscription à l'Ordre : {doctor.license_number}",
        f"- Années d'expérience : {doctor.years_experience}",
        f"- Ville d'exercice : {doctor.city}",
        f"- Inscription sur MediConnect : {timezone.localtime(doctor.submitted_at):%d/%m/%Y}",
    ])


REFERENCES_DIR = Path(__file__).resolve().parent / "references"
REFERENCES = [
    ("modele-diplome.jpg", "MODÈLE DE RÉFÉRENCE — diplôme de docteur en médecine (forme attendue)"),
    ("modele-ordre.jpg", "MODÈLE DE RÉFÉRENCE — document officiel du Conseil National de l'Ordre des médecins (en-tête attendu)"),
]


def reference_blocks():
    blocks = []
    for filename, caption in REFERENCES:
        path = REFERENCES_DIR / filename
        if path.exists():
            blocks.append({"type": "text", "text": caption + " :"})
            blocks.append({"type": "image", "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                           "mime_type": "image/jpeg"})
    return blocks


def is_sendable(data, ext):
    """A broken file makes OpenAI reject the whole request: only send well-formed ones."""
    if data is None or len(data) < 200 or not data.startswith(SIGNATURES.get(ext, (b"\0\0\0\0\0",))):
        return False
    if ext == ".pdf":
        return b"%%EOF" in data[-4096:] or b"%%EOF" in data
    return True


def document_blocks(contents):
    """Message content blocks: a caption then the file (PDF or image) for each document."""
    blocks = []
    for index, (field, (label, data, ext)) in enumerate(contents.items(), start=1):
        if not is_sendable(data, ext):
            blocks.append({"type": "text", "text": f"Document {index} — {label} : fichier vide, corrompu ou invalide (non joint)."})
            continue
        blocks.append({"type": "text", "text": f"Document {index} — {label} :"})
        encoded = base64.b64encode(data).decode("ascii")
        if ext == ".pdf":
            blocks.append({"type": "file", "base64": encoded, "mime_type": "application/pdf", "filename": f"{field}.pdf"})
        else:
            blocks.append({"type": "image", "base64": encoded, "mime_type": MIME_TYPES[ext]})
    return blocks


def build_tools(rule_checks):
    @tool
    def controles_automatiques() -> str:
        """Renvoie les contrôles automatiques faits par le serveur MediConnect sur ce dossier
        (validité des fichiers, fichiers réutilisés par un autre compte, format du numéro d'inscription,
        e-mail jetable, téléphone partagé, homonymes, expérience, présentation)."""
        return json.dumps(
            [{"controle": c["label"], "resultat": c["status"], "detail": c["detail"]} for c in rule_checks],
            ensure_ascii=False,
        )

    return [controles_automatiques]


def run_agent(doctor, contents, rule_checks):
    """Run the agent and return an AIVerificationOutput. Raises on any failure."""
    model = ChatOpenAI(
        model=settings.VERIFICATION_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
        timeout=settings.VERIFICATION_AI_TIMEOUT,
        max_retries=1,
    )
    agent = create_agent(
        model,
        tools=build_tools(rule_checks),
        system_prompt=SYSTEM_PROMPT,
        response_format=AIVerificationOutput,
    )
    content = [
        {"type": "text", "text": describe_declared(doctor)},
        *reference_blocks(),
        {"type": "text", "text": "DOCUMENTS ENVOYÉS PAR LE MÉDECIN :"},
        *document_blocks(contents),
    ]
    state = agent.invoke({"messages": [{"role": "user", "content": content}]})
    return state["structured_response"]


def to_checks(output):
    return [
        {"key": key, "label": label, "status": getattr(output, key).status,
         "detail": getattr(output, key).detail, "source": "ai"}
        for key, label in AI_LABELS.items()
    ]
