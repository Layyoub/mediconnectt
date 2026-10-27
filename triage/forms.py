from decimal import Decimal

from django import forms
from django.db import transaction

from core.forms import StyledFormMixin

from .models import Duration, RiskFactor, Severity, SymptomEntry, TriageSession
from .symptoms import SYMPTOMS, grouped_symptoms


class ContextForm(StyledFormMixin, forms.ModelForm):
    """Step 1: main complaint, temperature and risk factors."""

    risk_factors = forms.MultipleChoiceField(
        label="Facteurs de risque",
        required=False,
        choices=RiskFactor.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = TriageSession
        fields = ["chief_complaint", "temperature", "risk_factors"]
        labels = {
            "chief_complaint": "Qu'est-ce qui vous amène aujourd'hui ?",
            "temperature": "Température (si vous l'avez mesurée)",
        }
        widgets = {
            "chief_complaint": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Ex. : J'ai de la fièvre et je tousse depuis trois jours…",
            }),
            "temperature": forms.NumberInput(attrs={"step": "0.1", "min": "34", "max": "43", "placeholder": "Ex. : 38,5"}),
        }

    def clean_chief_complaint(self):
        text = self.cleaned_data["chief_complaint"].strip()
        if len(text) < 5:
            raise forms.ValidationError("Décrivez votre problème en quelques mots.")
        return text

    def clean_temperature(self):
        value = self.cleaned_data.get("temperature")
        if value is not None and not (Decimal("34") <= value <= Decimal("43")):
            raise forms.ValidationError("Indiquez une température entre 34 et 43 °C.")
        return value


class SymptomsForm(forms.Form):
    """Step 2: for each symptom, a checkbox plus its severity and duration.

    Fields are generated from the catalog: `s_<code>`, `sev_<code>`, `dur_<code>`.
    """

    def __init__(self, *args, session, **kwargs):
        self.session = session
        super().__init__(*args, **kwargs)
        existing = {e.symptom: e for e in session.symptoms.all()}
        for symptom in SYMPTOMS:
            entry = existing.get(symptom.code)
            self.fields[f"s_{symptom.code}"] = forms.BooleanField(
                label=symptom.label, required=False, initial=entry is not None
            )
            self.fields[f"sev_{symptom.code}"] = forms.TypedChoiceField(
                label="Intensité", required=False, coerce=int, choices=Severity.choices,
                widget=forms.RadioSelect, initial=entry.severity if entry else Severity.MODERATE,
            )
            self.fields[f"dur_{symptom.code}"] = forms.ChoiceField(
                label="Depuis", required=False,
                choices=[("", "Depuis quand ?")] + Duration.choices,
                initial=entry.duration if entry else "",
            )

    def grouped(self):
        """Bound fields grouped by category, for the template."""
        return [
            {
                "code": code, "label": label, "icon": icon,
                "items": [
                    {
                        "symptom": s,
                        "check": self[f"s_{s.code}"],
                        "severity": self[f"sev_{s.code}"],
                        "duration": self[f"dur_{s.code}"],
                    }
                    for s in symptoms
                ],
            }
            for code, label, icon, symptoms in grouped_symptoms()
        ]

    def clean(self):
        cleaned = super().clean()
        self.selected = []
        for symptom in SYMPTOMS:
            if not cleaned.get(f"s_{symptom.code}"):
                continue
            severity = cleaned.get(f"sev_{symptom.code}")
            duration = cleaned.get(f"dur_{symptom.code}")
            if not severity:
                self.add_error(f"sev_{symptom.code}", "Choisissez l'intensité.")
            if not duration:
                self.add_error(f"dur_{symptom.code}", "Indiquez depuis quand.")
            if severity and duration:
                self.selected.append((symptom.code, severity, duration))
        if not any(cleaned.get(f"s_{s.code}") for s in SYMPTOMS):
            raise forms.ValidationError("Sélectionnez au moins un symptôme.")
        return cleaned

    @transaction.atomic
    def save(self):
        self.session.symptoms.all().delete()
        SymptomEntry.objects.bulk_create([
            SymptomEntry(session=self.session, symptom=code, severity=severity, duration=duration)
            for code, severity, duration in self.selected
        ])
