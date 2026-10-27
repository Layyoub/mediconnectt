"""Deterministic checks on a doctor's application (no AI, nothing leaves the server)."""

import hashlib
import re

from core.models import DoctorProfile, PatientProfile

DOCUMENTS = [
    ("diploma", "Diplôme de médecine"),
    ("license_document", "Attestation d'inscription à l'Ordre"),
]

SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}
MIME_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MIN_REAL_SIZE = 10 * 1024  # a real scan or photo is rarely smaller than 10 KB

DISPOSABLE_DOMAINS = {
    "yopmail.com", "yopmail.fr", "mailinator.com", "guerrillamail.com", "guerrillamail.info", "sharklasers.com",
    "10minutemail.com", "10minutemail.net", "tempmail.com", "temp-mail.org", "trashmail.com", "trashmail.fr",
    "jetable.org", "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com", "fakeinbox.com",
    "mailnesia.com", "mintemail.com", "mohmal.com", "emailondeck.com", "tempail.com", "spamgourmet.com",
}

LICENSE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 /.\-]{2,29}$")


def _check(key, label, status, detail):
    return {"key": key, "label": label, "status": status, "detail": detail, "source": "rules"}


def read_document(field_file):
    """(bytes, extension) of an uploaded document, or (None, ext) if it cannot be read."""
    ext = ("." + field_file.name.rsplit(".", 1)[-1].lower()) if field_file and "." in field_file.name else ""
    if not field_file:
        return None, ext
    try:
        with field_file.open("rb") as handle:
            return handle.read(), ext
    except (FileNotFoundError, OSError, ValueError):
        return None, ext


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _digits(phone):
    return re.sub(r"\D", "", phone or "")


def document_contents(doctor):
    """{field: (label, bytes|None, ext)} for the two credential documents."""
    result = {}
    for field, label in DOCUMENTS:
        data, ext = read_document(getattr(doctor, field))
        result[field] = (label, data, ext)
    return result


def run_rules(doctor, contents=None):
    contents = contents or document_contents(doctor)
    checks = []
    user = doctor.user

    # 1. Files are present, readable, of the announced type and not (almost) empty.
    problems, warnings = [], []
    for field, (label, data, ext) in contents.items():
        if data is None:
            problems.append(f"{label} : fichier introuvable ou illisible")
        elif not data.startswith(SIGNATURES.get(ext, (b"\0\0\0\0\0",))):
            problems.append(f"{label} : le contenu ne correspond pas à un fichier {ext.lstrip('.').upper() or 'connu'}")
        elif len(data) < 200 or (ext == ".pdf" and b"%%EOF" not in data):
            problems.append(f"{label} : fichier vide ou corrompu ({len(data)} octets)")
        elif len(data) < MIN_REAL_SIZE:
            warnings.append(f"{label} : fichier très léger ({len(data)} octets), probablement vide ou factice")
    if problems:
        checks.append(_check("files_valid", "Fichiers des justificatifs", "fail", " ; ".join(problems + warnings) + "."))
    elif warnings:
        checks.append(_check("files_valid", "Fichiers des justificatifs", "warning", " ; ".join(warnings) + "."))
    else:
        checks.append(_check("files_valid", "Fichiers des justificatifs", "ok", "Les 2 fichiers sont lisibles et de type valide."))

    # 2. The same file is not used for two different documents.
    hashes = {field: _digest(data) for field, (_, data, _) in contents.items() if data}
    if len(set(hashes.values())) < len(hashes):
        checks.append(_check("files_distinct", "Justificatifs distincts", "fail",
                             "Le même fichier a été envoyé pour plusieurs justificatifs différents."))
    else:
        checks.append(_check("files_distinct", "Justificatifs distincts", "ok", "Chaque justificatif est un fichier différent."))

    # 3. The files were not already used by another doctor account.
    reused = []
    own = set(hashes.values())
    for other in DoctorProfile.objects.exclude(pk=doctor.pk).select_related("user"):
        for field, label in DOCUMENTS:
            data, _ = read_document(getattr(other, field))
            if data and _digest(data) in own:
                reused.append(f"Dr {other.user.get_full_name()} ({label.lower()})")
    if reused:
        checks.append(_check("files_reused", "Fichiers déjà utilisés", "fail",
                             "Fichier identique déjà présent dans le dossier de : " + ", ".join(sorted(set(reused))[:5]) + "."))
    else:
        checks.append(_check("files_reused", "Fichiers déjà utilisés", "ok", "Aucun fichier n'apparaît dans un autre dossier."))

    # 4. Licence number looks like a registration number.
    number = (doctor.license_number or "").strip().upper()
    digits = sum(c.isdigit() for c in number)
    if LICENSE_PATTERN.match(number) and digits >= 3:
        checks.append(_check("license_format", "Format du n° d'inscription", "ok", f"« {number} » a un format plausible."))
    else:
        checks.append(_check("license_format", "Format du n° d'inscription", "warning",
                             f"« {number} » ne ressemble pas à un numéro d'inscription (au moins 3 chiffres attendus)."))

    # 5. E-mail from a disposable mailbox service.
    domain = user.email.rsplit("@", 1)[-1].lower() if "@" in user.email else ""
    if domain in DISPOSABLE_DOMAINS:
        checks.append(_check("email_domain", "Adresse e-mail", "warning",
                             f"Le domaine « {domain} » est un service d'adresses jetables."))
    else:
        checks.append(_check("email_domain", "Adresse e-mail", "ok", "Adresse e-mail non jetable."))

    # 6. Phone number shared with other accounts.
    phone = _digits(doctor.phone)
    shared = 0
    if len(phone) >= 6:
        shared = sum(1 for p in DoctorProfile.objects.exclude(pk=doctor.pk).values_list("phone", flat=True) if _digits(p) == phone)
        shared += sum(1 for p in PatientProfile.objects.exclude(user=user).values_list("phone", flat=True) if _digits(p) == phone)
    if shared:
        checks.append(_check("phone_shared", "Numéro de téléphone", "warning",
                             f"Ce numéro est aussi utilisé par {shared} autre{'s' if shared > 1 else ''} compte{'s' if shared > 1 else ''}."))
    else:
        checks.append(_check("phone_shared", "Numéro de téléphone", "ok", "Numéro propre à ce compte."))

    # 7. Another doctor account with the same name.
    same_name = DoctorProfile.objects.exclude(pk=doctor.pk).filter(
        user__first_name__iexact=user.first_name, user__last_name__iexact=user.last_name
    ).count()
    if same_name:
        checks.append(_check("name_duplicate", "Homonymes", "warning",
                             f"{same_name} autre compte médecin porte le même nom : possible doublon."))
    else:
        checks.append(_check("name_duplicate", "Homonymes", "ok", "Aucun autre compte médecin à ce nom."))

    # 8. Declared experience.
    if doctor.years_experience > 50:
        checks.append(_check("experience", "Expérience déclarée", "warning",
                             f"{doctor.years_experience} ans d'expérience : valeur inhabituelle."))
    else:
        checks.append(_check("experience", "Expérience déclarée", "ok", f"{doctor.years_experience} an(s), valeur plausible."))

    # 9. Presentation text copied from another doctor.
    bio = " ".join((doctor.bio or "").lower().split())
    if not bio:
        checks.append(_check("bio", "Présentation", "info", "Pas de présentation (facultative)."))
    elif len(bio) > 40 and any(
        " ".join((b or "").lower().split()) == bio
        for b in DoctorProfile.objects.exclude(pk=doctor.pk).values_list("bio", flat=True)
    ):
        checks.append(_check("bio", "Présentation", "warning", "Texte identique à la présentation d'un autre médecin."))
    else:
        checks.append(_check("bio", "Présentation", "ok", "Présentation originale."))

    return checks


def risk_from_checks(checks):
    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        return "high"
    if statuses.count("warning") >= 2:
        return "medium"
    return "low"
