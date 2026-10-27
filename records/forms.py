import re

from django import forms
from django.utils import timezone

from core.forms import StyledFormMixin

from .access import CARE_STATUSES
from .models import ConsultationNote, Drug, MedicalDocument, MedicalRecord, Prescription, PrescriptionItem

MAGIC_BYTES = {
    "pdf": [b"%PDF"],
    "png": [b"\x89PNG"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
}


class MedicalRecordForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = MedicalRecord
        fields = [
            "blood_type", "height_cm", "weight_kg", "allergies", "chronic_conditions",
            "current_treatments", "medical_history", "family_history", "emergency_contact",
        ]
        widgets = {
            "allergies": forms.Textarea(attrs={"rows": 2, "placeholder": "Ex. : pénicilline (urticaire), arachide. Laissez vide si aucune."}),
            "chronic_conditions": forms.Textarea(attrs={"rows": 2, "placeholder": "Ex. : asthme, diabète de type 2, hypertension…"}),
            "current_treatments": forms.Textarea(attrs={"rows": 2, "placeholder": "Médicaments pris régulièrement et leur dosage."}),
            "medical_history": forms.Textarea(attrs={"rows": 2, "placeholder": "Opérations, hospitalisations, maladies passées…"}),
            "family_history": forms.Textarea(attrs={"rows": 2, "placeholder": "Ex. : diabète chez un parent, maladie cardiaque…"}),
            "emergency_contact": forms.TextInput(attrs={"placeholder": "Nom, lien et téléphone"}),
            "height_cm": forms.NumberInput(attrs={"placeholder": "170"}),
            "weight_kg": forms.NumberInput(attrs={"placeholder": "65", "step": "0.1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["blood_type"].choices = [("", "Je ne sais pas")] + list(MedicalRecord.BloodType.choices)


class NoteForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ConsultationNote
        fields = ["title", "appointment", "body", "visible_to_patient"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Ex. : Suivi de l'angine"}),
            "body": forms.Textarea(attrs={"rows": 6, "placeholder": "Évolution, examen, hypothèses, conduite à tenir…"}),
        }

    def __init__(self, *args, doctor, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["appointment"] = AppointmentChoiceField(doctor, patient)


class AppointmentChoiceField(forms.ModelChoiceField):
    """Optional link to one of this doctor's consultations with the patient."""

    def __init__(self, doctor, patient, **kwargs):
        from appointments.models import Appointment

        qs = Appointment.objects.filter(doctor=doctor, patient=patient, status__in=CARE_STATUSES).order_by("-start")
        super().__init__(
            qs, required=False, label="Consultation liée", empty_label="Aucune (hors consultation)", **kwargs
        )
        self.widget.attrs["class"] = "form-control form-select"

    def label_from_instance(self, obj):
        local = timezone.localtime(obj.start)
        return f"{local:%d/%m/%Y à %H:%M} — {obj.reason[:50]}"


class DocumentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = MedicalDocument
        fields = ["title", "kind", "document_date", "file"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Ex. : Bilan sanguin de contrôle"}),
            "document_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "file": forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}),
        }

    def clean_document_date(self):
        value = self.cleaned_data.get("document_date")
        if value and value > timezone.localdate():
            raise forms.ValidationError("La date ne peut pas être dans le futur.")
        return value

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if not file:
            return file
        ext = file.name.rsplit(".", 1)[-1].lower()
        head = file.read(8)
        file.seek(0)
        if not any(head.startswith(sig) for sig in MAGIC_BYTES.get(ext, [])):
            raise forms.ValidationError("Le contenu du fichier ne correspond pas à un PDF, JPG ou PNG.")
        return file


class SharedDocumentForm(DocumentForm):
    """Document sent from the consultation room: only the file is required."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
        self.fields["kind"].required = False

    def clean_title(self):
        title = (self.cleaned_data.get("title") or "").strip()
        if not title and self.files.get("file"):
            title = self.files["file"].name.rsplit(".", 1)[0][:150]
        return title or "Document"

    def clean_kind(self):
        return self.cleaned_data.get("kind") or MedicalDocument.Kind.ANALYSIS


class PrescriptionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Prescription
        fields = ["appointment", "notes", "valid_until"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Conseils pour le patient (à jeun, éviter le soleil, reconsulter si…)"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {"valid_until": "Par défaut, 3 mois."}

    def __init__(self, *args, doctor, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["appointment"] = AppointmentChoiceField(doctor, patient)

    def clean_valid_until(self):
        value = self.cleaned_data["valid_until"]
        today = timezone.localdate()
        if value < today:
            raise forms.ValidationError("La date de validité est déjà passée.")
        if (value - today).days > 366:
            raise forms.ValidationError("Une ordonnance est valable au maximum un an.")
        return value


DRUG_DATALIST_ID = "drug-suggestions"
# Words describing the form, not the substance: ignored by the allergy check.
FORM_WORDS = {
    "comprimé", "comprimés", "gélule", "gélules", "sirop", "sachet", "sachets", "gouttes", "spray",
    "crème", "pommade", "collyre", "injectable", "solution", "suppositoire", "inhalateur", "buvable",
}


def match_catalogue(text):
    """Catalogue entry for what the doctor typed: exact "name strength" first, otherwise a
    catalogue name contained in the text ("amoxicilline 750 mg" -> Amoxicilline). None if unknown."""
    typed = " ".join(text.lower().split())
    drugs = list(Drug.objects.all())
    for drug in drugs:
        if drug.label.lower() == typed:
            return drug
    for drug in sorted(drugs, key=lambda d: len(d.name), reverse=True):
        if re.search(rf"\b{re.escape(drug.name.lower())}\b", typed):
            return drug
    return None


class PrescriptionItemForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PrescriptionItem
        fields = ["drug_name", "dosage", "frequency", "duration", "instructions"]
        widgets = {
            "drug_name": forms.TextInput(attrs={
                "list": DRUG_DATALIST_ID, "autocomplete": "off",
                "placeholder": "Tapez le nom, ex. : Paracétamol 1 g",
            }),
            "dosage": forms.TextInput(attrs={"placeholder": "1 comprimé"}),
            "frequency": forms.TextInput(attrs={"placeholder": "3 fois par jour"}),
            "duration": forms.TextInput(attrs={"placeholder": "5 jours"}),
            "instructions": forms.TextInput(attrs={"placeholder": "Pendant les repas"}),
        }
        help_texts = {"drug_name": "Nom, dosage et forme. Des suggestions s'affichent pendant la saisie."}

    def clean_drug_name(self):
        return " ".join(self.cleaned_data["drug_name"].split())

    def save(self, commit=True):
        self.instance.drug = match_catalogue(self.instance.drug_name)
        return super().save(commit)


class BasePrescriptionItemFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        names = [f.cleaned_data["drug_name"].lower() for f in self.forms if f.cleaned_data.get("drug_name")]
        if not names:
            raise forms.ValidationError("Ajoutez au moins un médicament.")
        if len(names) != len(set(names)):
            raise forms.ValidationError("Un même médicament figure deux fois sur l'ordonnance.")


PrescriptionItemFormSet = forms.inlineformset_factory(
    Prescription, PrescriptionItem, form=PrescriptionItemForm, formset=BasePrescriptionItemFormSet,
    extra=0, min_num=1, max_num=10, validate_max=True, can_delete=False,
)


def allergy_conflicts(record, names):
    """Typed drugs that match the patient's allergies: by the words typed, and, when the drug
    is in the catalogue, by its active ingredient and class (amoxicilline -> pénicillines)."""
    text = (record.allergies or "").lower()
    if not text:
        return []

    def mentioned(term):
        stem = term[:-1] if term.endswith("s") and len(term) > 4 else term  # "pénicillines" ~ "pénicilline"
        return re.search(rf"\b{re.escape(stem)}s?\b", text) is not None

    conflicts = []
    for name in names:
        drug = match_catalogue(name)
        terms = {w for w in re.findall(r"[^\W\d_]{5,}", name.lower()) if w not in FORM_WORDS}
        if drug:
            terms |= drug.allergy_terms()
        if any(mentioned(term) for term in terms):
            conflicts.append({"name": name, "drug_class": drug.drug_class if drug else ""})
    return conflicts
