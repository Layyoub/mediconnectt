"""Optional AI task suggestions. Suggestions must be reviewed by the doctor."""
from pydantic import BaseModel, Field


class SuggestedTask(BaseModel):
    title: str = Field(max_length=160)
    details: str = Field(default="", max_length=500)
    days_from_now: int = Field(ge=0, le=90)


class FollowUpSuggestions(BaseModel):
    tasks: list[SuggestedTask] = Field(min_length=1, max_length=5)


SYSTEM_PROMPT = """Tu aides un médecin à préparer des tâches administratives de suivi après une consultation.
Propose au maximum cinq tâches simples et non diagnostiques, en français, à partir du motif fourni.
Ne prescris jamais de médicament, de dose, de traitement ni de modification thérapeutique.
Ne crée pas de tâche qui remplace un avis médical. Privilégie les rappels de rendez-vous,
la préparation de documents, et le suivi de mesures déjà demandées par le médecin.
Les résultats sont des brouillons à vérifier par le médecin avant enregistrement."""


def suggest_tasks(appointment):
    from django.conf import settings
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage

    model = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.2,
        timeout=settings.FOLLOWUP_AI_TIMEOUT,
        max_retries=1,
    ).with_structured_output(FollowUpSuggestions)
    return model.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(
            f"Motif de consultation : {appointment.reason[:1000]}\n"
            "Propose des tâches de suivi à discuter avec le patient. N'invente pas de diagnostic."
        )),
    ])
