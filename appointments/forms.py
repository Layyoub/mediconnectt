from datetime import datetime

from django import forms
from django.utils import timezone

from core.forms import StyledFormMixin
from triage.models import TriageSession

from .models import Availability, Weekday
from .slots import find_slot


class TriageChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{timezone.localtime(obj.completed_at):%d/%m/%Y} · {obj.result.get_level_display()} · {obj.chief_complaint[:50]}"


class BookingForm(StyledFormMixin, forms.Form):
    """Patient side: pick one free slot, give a reason, optionally share an assessment."""

    slot = forms.CharField(error_messages={"required": "Choisissez un créneau."})
    reason = forms.CharField(
        label="Motif de la consultation",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Ex. : fièvre et toux depuis 3 jours…"}),
    )
    triage_session = TriageChoiceField(
        label="Partager une évaluation avec le médecin",
        queryset=TriageSession.objects.none(),
        required=False,
        empty_label="Ne pas partager d'évaluation",
        help_text="Le médecin verra vos symptômes et le niveau d'urgence avant de valider.",
    )

    def __init__(self, *args, doctor, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.doctor = doctor
        self.fields["triage_session"].queryset = patient.triage_sessions.filter(
            status=TriageSession.Status.COMPLETED
        ).select_related("result")

    def clean_slot(self):
        try:
            start = datetime.fromisoformat(self.cleaned_data["slot"])
        except ValueError:
            raise forms.ValidationError("Créneau invalide.")
        slot = find_slot(self.doctor, start)
        if slot is None:
            raise forms.ValidationError("Ce créneau n'est plus disponible. Choisissez-en un autre.")
        return slot

    def clean_reason(self):
        text = self.cleaned_data["reason"].strip()
        if len(text) < 5:
            raise forms.ValidationError("Décrivez le motif en quelques mots.")
        return text


class DecisionForm(StyledFormMixin, forms.Form):
    """Doctor side: accept or refuse a request."""

    decision = forms.ChoiceField(choices=[("accept", "Accepter"), ("refuse", "Refuser")])
    message = forms.CharField(
        label="Message au patient",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Facultatif si vous acceptez, obligatoire si vous refusez."}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "refuse" and not cleaned.get("message", "").strip():
            self.add_error("message", "Expliquez au patient pourquoi vous refusez (il pourra choisir un autre créneau).")
        return cleaned


class CancelForm(StyledFormMixin, forms.Form):
    message = forms.CharField(
        label="Motif de l'annulation", required=False, max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Facultatif"}),
    )


class AvailabilityForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Availability
        fields = ["weekday", "start_time", "end_time", "slot_minutes"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def __init__(self, *args, doctor, **kwargs):
        kwargs.setdefault("initial", {"start_time": "09:00", "end_time": "12:00"})
        super().__init__(*args, **kwargs)
        self.fields["weekday"].choices = Weekday.choices  # no empty option
        self.instance.doctor = doctor
