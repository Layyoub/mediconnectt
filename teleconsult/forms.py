from django import forms

from core.forms import StyledFormMixin

from .models import ConsultationSummary


class SummaryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ConsultationSummary
        fields = ["diagnosis", "observations", "recommendations", "follow_up"]
        widgets = {
            "diagnosis": forms.Textarea(attrs={"rows": 2, "placeholder": "Ex. : syndrome grippal probable, sans signe de gravité."}),
            "observations": forms.Textarea(attrs={"rows": 3, "placeholder": "Éléments relevés pendant l'échange (symptômes, évolution, antécédents…)"}),
            "recommendations": forms.Textarea(attrs={"rows": 3, "placeholder": "Repos, hydratation, surveillance de la température… Quand reconsulter."}),
            "follow_up": forms.TextInput(attrs={"placeholder": "Ex. : nouvelle téléconsultation dans 7 jours si pas d'amélioration"}),
        }
        help_texts = {
            "recommendations": "Ce texte est lu par le patient : utilisez des mots simples.",
        }
