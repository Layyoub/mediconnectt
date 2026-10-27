import os
import re
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from appointments.models import Appointment

ALLOWED_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"]
PRESCRIPTION_VALIDITY = timedelta(days=90)


class MedicalRecord(models.Model):
    """The patient's health file: one per patient, created on first use."""

    class BloodType(models.TextChoices):
        A_POS = "A+", "A+"
        A_NEG = "A-", "A−"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B−"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB−"
        O_POS = "O+", "O+"
        O_NEG = "O-", "O−"

    patient = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="medical_record", verbose_name="patient"
    )
    blood_type = models.CharField("groupe sanguin", max_length=3, choices=BloodType.choices, blank=True)
    height_cm = models.PositiveSmallIntegerField(
        "taille (cm)", null=True, blank=True, validators=[MinValueValidator(40), MaxValueValidator(250)]
    )
    weight_kg = models.DecimalField(
        "poids (kg)", max_digits=4, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(2), MaxValueValidator(400)],
    )
    allergies = models.TextField("allergies", blank=True)
    chronic_conditions = models.TextField("maladies chroniques", blank=True)
    current_treatments = models.TextField("traitements en cours", blank=True)
    medical_history = models.TextField("antécédents médicaux et chirurgicaux", blank=True)
    family_history = models.TextField("antécédents familiaux", blank=True)
    emergency_contact = models.CharField("personne à prévenir", max_length=150, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("mis à jour le", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="mis à jour par",
    )

    class Meta:
        verbose_name = "dossier médical"
        verbose_name_plural = "dossiers médicaux"

    def __str__(self):
        return f"Dossier de {self.patient.get_full_name() or self.patient.email}"

    @classmethod
    def for_patient(cls, patient):
        record, _ = cls.objects.get_or_create(patient=patient)
        return record

    @property
    def bmi(self):
        if not self.height_cm or not self.weight_kg:
            return None
        meters = self.height_cm / 100
        return round(float(self.weight_kg) / (meters * meters), 1)

    @property
    def is_empty(self):
        return not any([
            self.blood_type, self.height_cm, self.weight_kg, self.allergies, self.chronic_conditions,
            self.current_treatments, self.medical_history, self.family_history,
        ])


class ConsultationNote(models.Model):
    """Clinical note written by a doctor in the patient's record."""

    record = models.ForeignKey(MedicalRecord, on_delete=models.CASCADE, related_name="notes", verbose_name="dossier")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="written_notes",
        verbose_name="médecin",
    )
    appointment = models.ForeignKey(
        Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="notes",
        verbose_name="consultation liée",
    )
    title = models.CharField("titre", max_length=150)
    body = models.TextField("note")
    visible_to_patient = models.BooleanField(
        "visible par le patient", default=True,
        help_text="Décochez pour une note de travail réservée aux médecins qui suivent le patient.",
    )
    created_at = models.DateTimeField("rédigée le", auto_now_add=True)

    class Meta:
        verbose_name = "note de consultation"
        verbose_name_plural = "notes de consultation"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Drug(models.Model):
    """Drug catalogue used to write prescriptions."""

    class Form(models.TextChoices):
        TABLET = "tablet", "Comprimé"
        CAPSULE = "capsule", "Gélule"
        SYRUP = "syrup", "Sirop"
        SACHET = "sachet", "Sachet"
        DROPS = "drops", "Gouttes"
        SPRAY = "spray", "Spray"
        INHALER = "inhaler", "Inhalateur"
        CREAM = "cream", "Crème / pommade"
        INJECTION = "injection", "Injectable"
        SUPPOSITORY = "suppository", "Suppositoire"
        OTHER = "other", "Autre"

    name = models.CharField("nom", max_length=120)
    dci = models.CharField("principe actif (DCI)", max_length=120, blank=True)
    drug_class = models.CharField(
        "classe", max_length=80, blank=True,
        help_text="Famille de médicaments (ex. : pénicillines, AINS), utilisée pour vérifier les allergies.",
    )
    form = models.CharField("forme", max_length=12, choices=Form.choices, default=Form.TABLET)
    strength = models.CharField("dosage", max_length=50, blank=True)
    is_active = models.BooleanField("disponible", default=True)

    class Meta:
        verbose_name = "médicament"
        verbose_name_plural = "médicaments"
        ordering = ["name", "strength"]
        constraints = [models.UniqueConstraint(fields=["name", "form", "strength"], name="unique_drug")]

    def __str__(self):
        label = f"{self.name} {self.strength}".strip()
        return f"{label} · {self.get_form_display().lower()}"

    @property
    def label(self):
        """What the doctor types / sees in the suggestions, e.g. "Paracétamol 1 g"."""
        return f"{self.name} {self.strength}".strip()

    def allergy_terms(self):
        """Words to look for in the patient's allergies (name, each active ingredient, class)."""
        parts = re.split(r"[,/]", f"{self.name},{self.dci},{self.drug_class}")
        return {p.strip().lower() for p in parts if len(p.strip()) > 2}


def _prescription_code():
    return f"ORD-{secrets.token_hex(4).upper()}"


def _default_valid_until():
    return timezone.localdate() + PRESCRIPTION_VALIDITY


class Prescription(models.Model):
    record = models.ForeignKey(
        MedicalRecord, on_delete=models.CASCADE, related_name="prescriptions", verbose_name="dossier"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="written_prescriptions",
        verbose_name="médecin",
    )
    appointment = models.ForeignKey(
        Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="prescriptions",
        verbose_name="consultation liée",
    )
    code = models.CharField("référence", max_length=16, unique=True, default=_prescription_code, editable=False)
    notes = models.TextField("recommandations", blank=True)
    valid_until = models.DateField("valable jusqu'au", default=_default_valid_until)
    created_at = models.DateTimeField("rédigée le", auto_now_add=True)

    class Meta:
        verbose_name = "ordonnance"
        verbose_name_plural = "ordonnances"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Ordonnance {self.code}"

    @property
    def patient(self):
        return self.record.patient

    @property
    def is_valid(self):
        return self.valid_until >= timezone.localdate()

    @property
    def title(self):
        names = [item.drug_name for item in self.items.all()]
        if not names:
            return "Ordonnance"
        return names[0] + (f" + {len(names) - 1}" if len(names) > 1 else "")


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name="items", verbose_name="ordonnance"
    )
    # Typed by the doctor. Linked to the catalogue when the name matches one of its entries
    # (used for the allergy check), but any drug can be prescribed.
    drug_name = models.CharField("médicament", max_length=150)
    drug = models.ForeignKey(
        Drug, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="fiche du catalogue"
    )
    dosage = models.CharField("posologie", max_length=80)
    frequency = models.CharField("fréquence", max_length=80)
    duration = models.CharField("durée", max_length=60)
    instructions = models.CharField("précisions", max_length=200, blank=True)

    class Meta:
        verbose_name = "ligne d'ordonnance"
        verbose_name_plural = "lignes d'ordonnance"
        ordering = ["id"]

    def __str__(self):
        return f"{self.drug_name} — {self.dosage}, {self.frequency}, {self.duration}"


def _document_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"dossiers/{instance.record.patient_id}/{uuid.uuid4().hex}{ext}"


def validate_document_size(file):
    if file.size > settings.MAX_UPLOAD_SIZE:
        raise ValidationError("Le fichier dépasse 5 Mo.")


class MedicalDocument(models.Model):
    """Lab results, imaging, letters… added by the patient or a doctor."""

    class Kind(models.TextChoices):
        ANALYSIS = "analysis", "Analyse biologique"
        IMAGING = "imaging", "Imagerie"
        REPORT = "report", "Compte rendu / courrier"
        PRESCRIPTION = "prescription", "Ordonnance externe"
        VACCINATION = "vaccination", "Vaccination"
        OTHER = "other", "Autre"

    ICONS = {
        "analysis": "droplet", "imaging": "radioactive", "report": "file-earmark-text",
        "prescription": "prescription2", "vaccination": "shield-plus", "other": "file-earmark",
    }

    record = models.ForeignKey(MedicalRecord, on_delete=models.CASCADE, related_name="documents", verbose_name="dossier")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+", verbose_name="ajouté par"
    )
    kind = models.CharField("type", max_length=12, choices=Kind.choices, default=Kind.ANALYSIS)
    title = models.CharField("titre", max_length=150)
    document_date = models.DateField("date du document", null=True, blank=True)
    file = models.FileField(
        "fichier", upload_to=_document_path,
        validators=[FileExtensionValidator(ALLOWED_EXTENSIONS), validate_document_size],
    )
    appointment = models.ForeignKey(
        Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents",
        verbose_name="partagé pendant la consultation",
    )
    original_name = models.CharField(max_length=200, blank=True, editable=False)
    size = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField("ajouté le", auto_now_add=True)

    class Meta:
        verbose_name = "document médical"
        verbose_name_plural = "documents médicaux"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def icon(self):
        return self.ICONS.get(self.kind, "file-earmark")

    @property
    def extension(self):
        return os.path.splitext(self.file.name)[1].lstrip(".").upper()

    @property
    def size_label(self):
        if self.size >= 1024 * 1024:
            return f"{self.size / 1024 / 1024:.1f} Mo"
        return f"{max(1, self.size // 1024)} Ko"


class AccessLog(models.Model):
    """Who opened or changed a medical record, and when (shown to the patient)."""

    class Action(models.TextChoices):
        VIEW = "view", "Consultation du dossier"
        UPDATE = "update", "Mise à jour des informations médicales"
        NOTE = "note", "Ajout d'une note"
        PRESCRIPTION = "prescription", "Rédaction d'une ordonnance"
        PRESCRIPTION_VIEW = "prescription_view", "Consultation d'une ordonnance"
        DOCUMENT_ADD = "document_add", "Ajout d'un document"
        DOCUMENT_VIEW = "document_view", "Ouverture d'un document"
        DOCUMENT_DELETE = "document_delete", "Suppression d'un document"
        DOCUMENT_ANALYSIS = "document_analysis", "Analyse IA d'un document"
        PRESCRIPTION_CHECK = "prescription_check", "Vérification de sécurité d'une ordonnance"
        RX_INSTRUCTIONS = "rx_instructions", "Consultation des consignes de prescription"

    ICONS = {
        "view": "eye", "update": "pencil", "note": "journal-text", "prescription": "prescription2",
        "prescription_view": "prescription2", "document_add": "file-earmark-plus",
        "document_view": "file-earmark", "document_delete": "trash", "document_analysis": "robot",
        "prescription_check": "shield-check",
        "rx_instructions": "translate",
    }

    record = models.ForeignKey(
        MedicalRecord, on_delete=models.CASCADE, related_name="access_logs", verbose_name="dossier"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+", verbose_name="utilisateur"
    )
    action = models.CharField("action", max_length=20, choices=Action.choices)
    detail = models.CharField("détail", max_length=200, blank=True)
    ip_address = models.GenericIPAddressField("adresse IP", null=True, blank=True)
    created_at = models.DateTimeField("date", auto_now_add=True)

    class Meta:
        verbose_name = "journal d'accès"
        verbose_name_plural = "journal des accès"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["record", "-created_at"])]

    def __str__(self):
        return f"{self.get_action_display()} · {self.created_at:%d/%m/%Y %H:%M}"

    @property
    def icon(self):
        return self.ICONS.get(self.action, "dot")


class DocumentAnalysis(models.Model):
    """Report written by the AI agent about a patient's document (lab results…), for the doctor."""

    class Status(models.TextChoices):
        RUNNING = "running", "En cours"
        DONE = "done", "Terminée"
        ERROR = "error", "Échec"

    document = models.ForeignKey(
        MedicalDocument, on_delete=models.CASCADE, related_name="analyses", verbose_name="document"
    )
    status = models.CharField("état", max_length=10, choices=Status.choices, default=Status.RUNNING)
    document_type = models.CharField("type de document détecté", max_length=150, blank=True)
    exam_date = models.CharField("date de l'examen", max_length=40, blank=True)
    readable = models.BooleanField("document lisible", default=True)
    # [{"name", "value", "unit", "reference", "flag": normal|high|low|abnormal|unknown, "comment"}]
    values = models.JSONField("valeurs relevées", default=list, blank=True)
    abnormal_findings = models.JSONField("anomalies", default=list, blank=True)
    points_to_check = models.JSONField("points à vérifier", default=list, blank=True)
    summary = models.TextField("synthèse", blank=True)
    ai_model = models.CharField("modèle", max_length=60, blank=True)
    error = models.CharField("erreur", max_length=300, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="demandée par",
    )
    created_at = models.DateTimeField("lancée le", auto_now_add=True)
    finished_at = models.DateTimeField("terminée le", null=True, blank=True)

    class Meta:
        verbose_name = "analyse IA de document"
        verbose_name_plural = "analyses IA de documents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Analyse IA · {self.document}"

    STALE_AFTER = timedelta(minutes=5)

    @property
    def is_running(self):
        return self.status == self.Status.RUNNING and timezone.now() - self.created_at < self.STALE_AFTER

    @property
    def is_stale(self):
        return self.status == self.Status.RUNNING and not self.is_running

    @property
    def abnormal_values(self):
        return [v for v in self.values if v.get("flag") in ("high", "low", "abnormal")]


class DrugInteraction(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", "Information"
        MINOR = "minor", "Mineure"
        MODERATE = "moderate", "Modérée"
        MAJOR = "major", "Majeure"
        CONTRAINDICATED = "contraindicated", "Contre-indiquée"

    group_a = models.CharField("DCI ou classe A", max_length=120)
    group_b = models.CharField("DCI ou classe B", max_length=120)
    severity = models.CharField("gravité", max_length=20, choices=Severity.choices)
    advice = models.TextField("conduite à tenir")

    class Meta:
        verbose_name = "interaction médicamenteuse"
        verbose_name_plural = "interactions médicamenteuses"
        ordering = ["group_a", "group_b"]

    def __str__(self):
        return f"{self.group_a} + {self.group_b} ({self.get_severity_display()})"


class DoseLimit(models.Model):
    dci = models.CharField("DCI", max_length=120)
    max_daily_mg = models.DecimalField(
        "dose maximale quotidienne (mg)", max_digits=9, decimal_places=2, null=True, blank=True
    )
    per_kg_daily_mg = models.DecimalField(
        "dose quotidienne (mg/kg)", max_digits=8, decimal_places=2, null=True, blank=True
    )
    min_age_months = models.PositiveIntegerField("âge minimum (mois)", null=True, blank=True)
    max_age_months = models.PositiveIntegerField("âge maximum (mois)", null=True, blank=True)
    note = models.TextField("note")

    class Meta:
        verbose_name = "limite de dose"
        verbose_name_plural = "limites de dose"
        ordering = ["dci", "min_age_months", "max_age_months"]

    def __str__(self):
        return f"{self.dci} · {self.note[:60]}"


class Contraindication(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", "Information"
        MINOR = "minor", "Mineure"
        MODERATE = "moderate", "Modérée"
        MAJOR = "major", "Majeure"
        CONTRAINDICATED = "contraindicated", "Contre-indiquée"

    group = models.CharField("DCI ou classe", max_length=120)
    keywords = models.JSONField("mots-clés cliniques", default=list, blank=True)
    min_age_months = models.PositiveIntegerField("âge minimum (mois)", null=True, blank=True)
    max_age_months = models.PositiveIntegerField("âge maximum (mois)", null=True, blank=True)
    severity = models.CharField("gravité", max_length=20, choices=Severity.choices)
    message = models.TextField("message")

    class Meta:
        verbose_name = "contre-indication"
        verbose_name_plural = "contre-indications"
        ordering = ["group"]

    def __str__(self):
        return f"{self.group} · {self.get_severity_display()}"


class PrescriptionCheck(models.Model):
    class AIStatus(models.TextChoices):
        IDLE = "idle", "Non demandée"
        RUNNING = "running", "En cours"
        DONE = "done", "Terminée"
        ERROR = "error", "Échec"

    prescription = models.ForeignKey(
        Prescription, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="safety_checks", verbose_name="ordonnance",
    )
    record = models.ForeignKey(
        MedicalRecord, on_delete=models.CASCADE, related_name="prescription_checks", verbose_name="dossier"
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="prescription_checks", verbose_name="médecin",
    )
    findings = models.JSONField("alertes de règles", default=list, blank=True)
    checked_items = models.JSONField("médicaments vérifiés", default=list, blank=True)
    confirmed_resolutions = models.JSONField("médicaments reconnus et confirmés", default=list, blank=True)
    max_severity = models.CharField(
        "gravité maximale", max_length=20, choices=DrugInteraction.Severity.choices,
        default=DrugInteraction.Severity.INFO,
    )
    override_reason = models.TextField("motif de dérogation", blank=True)
    ai_opinion = models.JSONField("avis IA", default=dict, blank=True)
    ai_status = models.CharField(
        "état de l'avis IA", max_length=10, choices=AIStatus.choices, default=AIStatus.IDLE
    )
    ai_error = models.CharField("erreur IA", max_length=300, blank=True)
    ai_model = models.CharField("modèle IA", max_length=60, blank=True)
    ai_cache_key = models.CharField("clé de cache IA", max_length=64, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        verbose_name = "vérification de prescription"
        verbose_name_plural = "vérifications de prescription"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Vérification · {self.record} · {self.created_at:%d/%m/%Y %H:%M}"

    STALE_AFTER = timedelta(minutes=5)

    @property
    def is_ai_stale(self):
        return (
            self.ai_status == self.AIStatus.RUNNING
            and timezone.now() - self.created_at >= self.STALE_AFTER
        )


class PatientInstructions(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        APPROVED = "approved", "Approuvées"

    class GenerationStatus(models.TextChoices):
        RUNNING = "running", "En cours"
        DONE = "done", "Terminée"
        ERROR = "error", "Échec"

    prescription = models.OneToOneField(
        Prescription, on_delete=models.CASCADE, related_name="patient_instructions", verbose_name="ordonnance"
    )
    text_fr = models.TextField("consignes en français", blank=True)
    text_ar = models.TextField("consignes en arabe", blank=True)
    status = models.CharField("validation", max_length=10, choices=Status.choices, default=Status.DRAFT)
    generation_status = models.CharField(
        "génération IA", max_length=10, choices=GenerationStatus.choices, default=GenerationStatus.RUNNING
    )
    generation_error = models.CharField("erreur de génération", max_length=300, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_patient_instructions", verbose_name="validées par",
    )
    approved_at = models.DateTimeField("validées le", null=True, blank=True)
    created_at = models.DateTimeField("créées le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiées le", auto_now=True)

    class Meta:
        verbose_name = "consignes patient de prescription"
        verbose_name_plural = "consignes patient de prescription"

    def __str__(self):
        return f"Consignes patient · {self.prescription.code}"

    @property
    def is_generation_stale(self):
        return (
            self.generation_status == self.GenerationStatus.RUNNING
            and timezone.now() - self.updated_at >= PrescriptionCheck.STALE_AFTER
        )
