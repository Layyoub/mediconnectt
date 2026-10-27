import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from records import access
from records.models import AccessLog, MedicalRecord, PrescriptionCheck

from . import ai_agent, engine
from .matching import normalize_text


def _failure(message, status):
    return JsonResponse({"error": message}, status=status)


def _response(check):
    opinion = check.ai_opinion or {}
    return JsonResponse({
        "check_id": check.pk,
        "findings": check.findings,
        "max_severity": check.max_severity,
        "rules_severity": engine.max_severity(check.findings),
        "ai_status": check.ai_status,
        "ai_error": check.ai_error,
        "ai_opinion": opinion,
        "final_level": opinion.get("final_level", check.max_severity),
    })


def validate_confirmed_resolutions(record, doctor, values, current_items):
    if not isinstance(values, list):
        raise ValueError("Les confirmations de médicaments sont invalides.")
    current_names = {normalize_text(item["drug_name"]) for item in current_items}
    confirmed = []
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Une confirmation de médicament est invalide.")
        try:
            source_id = int(value.get("source_check_id"))
        except (TypeError, ValueError):
            raise ValueError("La vérification IA associée à une confirmation est invalide.") from None
        typed = str(value.get("typed", "")).strip()
        if not typed or normalize_text(typed) not in current_names:
            raise ValueError("La confirmation ne correspond pas à un médicament saisi.")
        source = PrescriptionCheck.objects.filter(
            pk=source_id,
            record=record,
            doctor=doctor,
            ai_status=PrescriptionCheck.AIStatus.DONE,
        ).first()
        if source is None:
            raise ValueError("La suggestion IA doit être confirmée depuis son résultat d'origine.")
        suggestion = next(
            (
                item for item in (source.ai_opinion or {}).get("resolved_drugs", [])
                if normalize_text(item.get("typed")) == normalize_text(typed)
            ),
            None,
        )
        if suggestion is None:
            raise ValueError("Aucune suggestion IA correspondante n'a été trouvée.")
        if (
            normalize_text(value.get("dci")) != normalize_text(suggestion["dci"])
            or normalize_text(value.get("drug_class")) != normalize_text(suggestion["drug_class"])
        ):
            raise ValueError("La DCI confirmée ne correspond pas à la suggestion affichée.")
        if normalize_text(typed) not in seen:
            confirmed.append({
                "typed": suggestion["typed"],
                "dci": suggestion["dci"],
                "drug_class": suggestion["drug_class"],
                "confidence": suggestion["confidence"],
                "note": suggestion["note"],
                "based_on": suggestion.get("based_on", []),
                "source_check_id": source.pk,
            })
            seen.add(normalize_text(typed))
    return confirmed


def valid_ai_source_check(record, doctor, source_id, current_items):
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        return None
    source = PrescriptionCheck.objects.filter(
        pk=source_id,
        record=record,
        doctor=doctor,
        ai_status=PrescriptionCheck.AIStatus.DONE,
    ).first()
    if source is None or source.checked_items != current_items or not source.ai_cache_key:
        return None
    expected_key = ai_agent.cache_key_for(
        record, source.checked_items, source.findings, source.confirmed_resolutions
    )
    return source if expected_key == source.ai_cache_key else None


@require_POST
def verify(request):
    if not request.user.is_authenticated:
        return _failure("Authentification requise.", 401)
    if not hasattr(request.user, "doctor_profile") or not request.user.doctor_profile.is_verified:
        return _failure("Accès réservé à un médecin vérifié.", 403)
    if request.content_type != "application/json":
        return _failure("Corps JSON requis.", 400)
    try:
        payload = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _failure("JSON invalide.", 400)
    if not isinstance(payload, dict):
        return _failure("Objet JSON requis.", 400)
    if "ai" in payload and type(payload["ai"]) is not bool:
        return _failure("Le champ ai doit être un booléen.", 400)

    record_id = payload.get("record_id")
    if type(record_id) is not int:
        return _failure("Dossier patient invalide.", 400)
    record = get_object_or_404(MedicalRecord.objects.select_related("patient__patient_profile"), pk=record_id)
    if not access.doctor_follows(request.user, record.patient):
        return _failure("Ce dossier n'est pas accessible à ce médecin.", 403)

    check_id = payload.get("check_id")
    if check_id is not None:
        try:
            check = PrescriptionCheck.objects.get(
                pk=int(check_id), record=record, doctor=request.user
            )
        except (TypeError, ValueError, PrescriptionCheck.DoesNotExist):
            return _failure("Vérification introuvable.", 404)
        ai_agent.mark_stale(check)
        check.refresh_from_db()
        return _response(check)

    items = payload.get("items")
    if not isinstance(items, list) or len(items) > 10:
        return _failure("La liste des médicaments est invalide (10 lignes maximum).", 400)
    checked_items = []
    for item in items:
        if not isinstance(item, dict):
            return _failure("Ligne de médicament invalide.", 400)
        checked_items.append({
            "drug_name": str(item.get("drug_name", "")).strip()[:150],
            "dosage": str(item.get("dosage", "")).strip()[:80],
            "frequency": str(item.get("frequency", "")).strip()[:80],
            "duration": str(item.get("duration", "")).strip()[:60],
        })

    try:
        confirmed_resolutions = validate_confirmed_resolutions(
            record, request.user, payload.get("confirmed_resolutions", []), checked_items
        )
    except ValueError as exc:
        return _failure(str(exc), 400)
    findings = engine.check(record, checked_items, confirmed_resolutions)
    cache_key = ai_agent.cache_key_for(record, checked_items, findings, confirmed_resolutions)
    if payload.get("ai") and settings.PHARMACHECK_AI_ENABLED:
        cached = PrescriptionCheck.objects.filter(
            record=record,
            doctor=request.user,
            ai_cache_key=cache_key,
            ai_status__in=[PrescriptionCheck.AIStatus.RUNNING, PrescriptionCheck.AIStatus.DONE],
        ).order_by("-created_at").first()
        if cached is not None:
            ai_agent.mark_stale(cached)
            cached.refresh_from_db()
            if cached.ai_status != PrescriptionCheck.AIStatus.ERROR:
                access.log(
                    request, record, AccessLog.Action.PRESCRIPTION_CHECK,
                    f"Vérification Pharma-Check en cache {cached.pk}",
                )
                return _response(cached)
    safety_check = PrescriptionCheck.objects.create(
        record=record,
        doctor=request.user,
        findings=findings,
        checked_items=checked_items,
        confirmed_resolutions=confirmed_resolutions,
        max_severity=engine.max_severity(findings),
        ai_cache_key=cache_key if payload.get("ai") else "",
    )
    access.log(
        request, record, AccessLog.Action.PRESCRIPTION_CHECK,
        f"Vérification Pharma-Check {safety_check.pk}",
    )
    if payload.get("ai"):
        if not settings.PHARMACHECK_AI_ENABLED:
            safety_check.ai_status = PrescriptionCheck.AIStatus.ERROR
            safety_check.ai_error = "Avis IA désactivé : clé OpenAI absente ou analyse désactivée."
            safety_check.save(update_fields=["ai_status", "ai_error"])
        else:
            safety_check = ai_agent.start_analysis(safety_check)
    return _response(safety_check)
