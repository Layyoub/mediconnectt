from django import forms
from django.utils import timezone

from core.forms import StyledFormMixin
from .models import FollowUpPlan, FollowUpTask


class FollowUpPlanForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = FollowUpPlan
        fields = ["title", "instructions"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Ex. : Suivi après consultation"}),
            "instructions": forms.Textarea(attrs={"rows": 3, "placeholder": "Conseils généraux convenus pendant la consultation…"}),
        }


class FollowUpTaskForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = FollowUpTask
        fields = ["title", "details", "due_date"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Ex. : Mesurer ma tension"}),
            "details": forms.TextInput(attrs={"placeholder": "Précisions utiles (facultatif)"}),
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def clean_due_date(self):
        due_date = self.cleaned_data.get("due_date")
        if due_date and due_date < timezone.localdate():
            raise forms.ValidationError("Choisissez aujourd'hui ou une date ultérieure.")
        return due_date


class TaskProgressForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = FollowUpTask
        fields = ["status"]
