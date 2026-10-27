"""Deterministic, editable medication-safety rules for clinician decision support."""

import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from records.ai_analysis import patient_context
from records.forms import allergy_conflicts
from records.models import (
    Contraindication,
    DoseLimit,
    Drug,
    DrugInteraction,
    Prescription,
)
from triage.models import TriageSession

from .matching import normalize_text, resolve_drug

SEVERITY_ORDER = ("info", "minor", "moderate", "major", "contraindicated")
SEVERITY_LABELS = {
    "info": "Information",
    "minor": "Mineure",
    "moderate": "Modérée",
    "major": "Majeure",
    "contraindicated": "Contre-indiquée",
}


def _value(item, key, default=""):
    if isinstance(item, dict):
        return item.get(key, default) or default
    return getattr(item, key, default) or default


def _age_months(record):
    profile = getattr(record.patient, "patient_profile", None)
    birth_date = getattr(profile, "date_of_birth", None)
    if not birth_date:
        return None
    today = timezone.localdate()
    return (today.year - birth_date.year) * 12 + today.month - birth_date.month - (today.day < birth_date.day)


def _split_treatments(text):
    return [part.strip() for part in re.split(r"[,;\n+]", text or "") if part.strip()]


def _group_values(drug):
    if not drug:
        return set()
    values = {
        normalize_text(drug.get("name")),
        normalize_text(drug.get("dci")),
        normalize_text(drug.get("drug_class")),
    }
    values.discard("")
    return values


def _matches_group(drug, group):
    return normalize_text(group) in _group_values(drug)


def _mg_amount(text):
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(mcg|µg|ug|mg|g)\b", str(text or "").lower())
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    unit = match.group(2)
    if unit == "g":
        amount *= 1000
    elif unit in ("mcg", "µg", "ug"):
        amount /= 1000
    return amount


def _frequency_per_day(text):
    normalized = normalize_text(text)
    match = re.search(
        r"\b(\d+(?:[.,]\d+)?)\s*(?:fois\s*(?:par\s*)?|/\s*|par\s*)?(?:jour|j)\b",
        normalized,
    )
    if match:
        try:
            return Decimal(match.group(1).replace(",", "."))
        except InvalidOperation:
            return None
    match = re.search(r"[x×*]\s*(\d+(?:[.,]\d+)?)\b", str(text or "").lower())
    if match:
        try:
            return Decimal(match.group(1).replace(",", "."))
        except InvalidOperation:
            return None
    return None


def _daily_dose(item, resolved):
    dosage = _value(item, "dosage")
    frequency = _value(item, "frequency")
    per_administration = _mg_amount(dosage)
    count = re.search(
        r"\b(\d+(?:[.,]\d+)?)\s*(?:comprim[eé]s?|g[eé]lules?|capsules?)\b",
        dosage,
        re.IGNORECASE,
    )
    if per_administration is not None and count:
        per_administration *= Decimal(count.group(1).replace(",", "."))
    elif per_administration is not None:
        units = re.search(r"\b(\d+(?:[.,]\d+)?)\s*[x×*]\s*\d+(?:[.,]\d+)?\s*(?:mg|g)\b", dosage, re.IGNORECASE)
        if units:
            per_administration *= Decimal(units.group(1).replace(",", "."))
    if per_administration is None and count:
        strength = _mg_amount(resolved.get("strength"))
        if strength:
            per_administration = Decimal(count.group(1).replace(",", ".")) * strength
    administrations = _frequency_per_day(frequency) or _frequency_per_day(dosage)
    if per_administration is None or administrations is None:
        return None
    return per_administration * administrations


def _age_applies(age, minimum, maximum):
    if minimum is not None and (age is None or age < minimum):
        return False
    if maximum is not None and (age is None or age > maximum):
        return False
    return True


def _finding(kind, severity, message, medications=(), **extra):
    return {
        "kind": kind,
        "severity": severity,
        "severity_label": SEVERITY_LABELS[severity],
        "message": str(message),
        "medications": list(medications),
        **extra,
    }


def _context_text(record):
    context = patient_context(record)
    return normalize_text(" ".join((
        record.allergies or "",
        record.chronic_conditions or "",
        record.medical_history or "",
        context.get("maladies_chroniques", ""),
    )))


def check(record, items, confirmed_resolutions=()):
    """Return rules findings for unsaved prescription items and the patient's current therapy."""
    catalog = list(Drug.objects.filter(is_active=True))
    resolved_items = []
    findings = []
    confirmed = {
        normalize_text(item["typed"]): {
            "name": item["dci"],
            "dci": item["dci"],
            "drug_class": item["drug_class"],
            "strength": "",
            "matched": True,
            "source": "doctor_confirmed",
        }
        for item in confirmed_resolutions
    }

    for item in items:
        name = str(_value(item, "drug_name")).strip()
        if not name:
            continue
        drug = confirmed.get(normalize_text(name)) or resolve_drug(
            name, _value(item, "drug"), catalog
        )
        resolved_items.append({"item": item, "name": name, "drug": drug})
        if drug is None:
            findings.append(_finding(
                "unresolved", "info",
                f"« {name} » n'a pas été reconnu dans les références disponibles ; aucune vérification spécifique n'a été effectuée.",
                (name,),
            ))

    names = [entry["name"] for entry in resolved_items]
    legacy_conflicts = allergy_conflicts(record, names)
    allergies = normalize_text(record.allergies)
    for entry in resolved_items:
        name, drug = entry["name"], entry["drug"]
        if not drug:
            continue
        allergy_terms = {
            normalize_text(name),
            normalize_text(drug.get("dci")),
            normalize_text(drug.get("drug_class")),
        }
        allergy_terms.discard("")
        drug_groups = _group_values(drug)
        allergy_groups = set()
        if "penicillin" in allergies or "penicilline" in allergies:
            allergy_groups.add("penicillines")
        if "cephalosporin" in allergies or "cephalosporine" in allergies:
            allergy_groups.add("cephalosporines")
        direct = any(
            re.search(rf"(?:^| ){re.escape(term)}(?:$| )", allergies)
            for term in allergy_terms
        ) or bool(allergy_groups & drug_groups)
        legacy = any(conflict["name"] == name for conflict in legacy_conflicts)
        cross = (
            ("penicillines" in allergy_groups and "cephalosporines" in drug_groups)
            or ("cephalosporines" in allergy_groups and "penicillines" in drug_groups)
        )
        if direct or legacy:
            findings.append(_finding(
                "allergy", "contraindicated",
                f"Allergie déclarée potentiellement liée à {name} ; vérifier le dossier avant toute décision.",
                (name,),
            ))
        elif cross:
            findings.append(_finding(
                "allergy", "moderate",
                f"Allergie croisée possible entre bêta-lactamines pour {name} ; évaluation clinique requise.",
                (name,),
            ))

    counts = {}
    for entry in resolved_items:
        if entry["drug"]:
            key = normalize_text(entry["drug"]["dci"])
            counts.setdefault(key, []).append(entry["name"])
    for names_for_dci in counts.values():
        if len(names_for_dci) > 1:
            findings.append(_finding(
                "duplicate_dci", "major",
                f"Plusieurs lignes peuvent contenir le même principe actif ({', '.join(names_for_dci)}).",
                names_for_dci,
            ))

    current_entries = []
    for treatment in _split_treatments(record.current_treatments):
        drug = resolve_drug(treatment, catalog=catalog)
        if drug:
            current_entries.append({"name": treatment, "drug": drug, "source": "traitement en cours"})
        else:
            findings.append(_finding(
                "unresolved", "info",
                f"Traitement en cours « {treatment} » non reconnu ; ses interactions n'ont pas pu être vérifiées.",
                (treatment,),
            ))
    valid_items = Prescription.objects.filter(
        record__patient=record.patient, valid_until__gte=timezone.localdate()
    ).values_list("items__drug_name", "items__drug__dci", "items__drug__drug_class", "items__drug__strength")
    for name, dci, drug_class, strength in valid_items:
        if name:
            drug = resolve_drug(
                name, {"dci": dci, "drug_class": drug_class, "strength": strength}, catalog
            )
            if drug is None:
                findings.append(_finding(
                    "unresolved", "info",
                    f"Médicament d'une ordonnance valide « {name} » non reconnu ; ses interactions n'ont pas pu être vérifiées.",
                    (name,),
                ))
                continue
            current_entries.append({
                "name": name,
                "drug": drug,
                "source": "ordonnance valide",
            })

    interactions = list(DrugInteraction.objects.all())
    pairs = []
    for index, first in enumerate(resolved_items):
        if first["drug"]:
            pairs.extend((first, second, "dans cette ordonnance") for second in resolved_items[index + 1:] if second["drug"])
            pairs.extend((first, other, other["source"]) for other in current_entries if other["drug"])
    reported = set()
    for first, second, source in pairs:
        for rule in interactions:
            forward = _matches_group(first["drug"], rule.group_a) and _matches_group(second["drug"], rule.group_b)
            reverse = _matches_group(first["drug"], rule.group_b) and _matches_group(second["drug"], rule.group_a)
            if not (forward or reverse):
                continue
            key = (rule.pk, tuple(sorted((first["name"], second["name"]))))
            if key in reported:
                continue
            reported.add(key)
            findings.append(_finding(
                "interaction", rule.severity,
                f"{first['name']} + {second['name']} ({source}) : {rule.advice}",
                (first["name"], second["name"]),
            ))

    age = _age_months(record)
    context_text = _context_text(record)
    for entry in resolved_items:
        drug = entry["drug"]
        if not drug:
            continue
        for limit in DoseLimit.objects.all():
            if normalize_text(limit.dci) != normalize_text(drug["dci"]):
                continue
            in_age = _age_applies(age, limit.min_age_months, limit.max_age_months)
            if not in_age and age is not None:
                continue
            if not in_age and age is None:
                findings.append(_finding(
                    "dose_unparsed", "info",
                    f"Âge requis pour interpréter la limite de dose de {entry['name']} ; vérification non concluante.",
                    (entry["name"],),
                ))
                continue
            daily_mg = _daily_dose(entry["item"], drug)
            if daily_mg is None:
                findings.append(_finding(
                    "dose_unparsed", "info",
                    f"Posologie de {entry['name']} non interprétable automatiquement ; vérification de dose nécessaire.",
                    (entry["name"],),
                ))
                continue
            caps = []
            if limit.max_daily_mg is not None:
                caps.append(Decimal(limit.max_daily_mg))
            if limit.per_kg_daily_mg is not None:
                weight = record.weight_kg
                if weight is None:
                    findings.append(_finding(
                        "dose_unparsed", "info",
                        f"Poids requis pour calculer la limite de dose de {entry['name']} ; vérification non concluante.",
                        (entry["name"],),
                    ))
                    continue
                caps.append(Decimal(limit.per_kg_daily_mg) * Decimal(weight))
            if caps and daily_mg > min(caps):
                findings.append(_finding(
                    "dose", "major",
                    f"La dose quotidienne saisie pour {entry['name']} dépasse la limite de référence configurée : {limit.note}",
                    (entry["name"],),
                    daily_mg=str(daily_mg),
                    reference_max_mg=str(min(caps)),
                ))

        for rule in Contraindication.objects.all():
            if not _matches_group(drug, rule.group):
                continue
            condition_matches = any(
                normalize_text(keyword) in context_text for keyword in rule.keywords if keyword
            )
            has_age_limit = rule.min_age_months is not None or rule.max_age_months is not None
            age_matches = (
                _age_applies(age, rule.min_age_months, rule.max_age_months)
                if has_age_limit else False
            )
            if condition_matches or age_matches:
                findings.append(_finding(
                    "contraindication", rule.severity, rule.message, (entry["name"],)
                ))
            elif has_age_limit and age is None:
                findings.append(_finding(
                    "age_unparsed", "info",
                    f"Âge non renseigné : la restriction d'âge configurée pour {entry['name']} n'a pas pu être vérifiée.",
                    (entry["name"],),
                ))

    findings.sort(key=lambda result: SEVERITY_ORDER.index(result["severity"]), reverse=True)
    return findings


def max_severity(findings):
    return max(
        (finding.get("severity", "info") for finding in findings),
        key=lambda severity: SEVERITY_ORDER.index(severity),
        default="info",
    )


def requires_override(findings):
    return max_severity(findings) in ("major", "contraindicated")
