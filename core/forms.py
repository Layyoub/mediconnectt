from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.db import transaction

from .models import DoctorProfile, PatientProfile

User = get_user_model()


class StyledFormMixin:
    """Add the design-system CSS class to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                css = "form-check-input"
            elif isinstance(widget, forms.Select):
                css = "form-control form-select"
            elif isinstance(widget, forms.ClearableFileInput):
                css = "form-file-input"
            else:
                css = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class EmailAuthenticationForm(StyledFormMixin, AuthenticationForm):
    username = forms.EmailField(
        label="Adresse e-mail",
        widget=forms.EmailInput(attrs={"autofocus": True, "placeholder": "nom@exemple.com", "autocomplete": "email"}),
    )
    password = forms.CharField(
        label="Mot de passe",
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "••••••••", "autocomplete": "current-password"}),
    )

    error_messages = {
        "invalid_login": "Adresse e-mail ou mot de passe incorrect.",
        "inactive": "Ce compte est désactivé.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].lower()


class BaseSignupForm(StyledFormMixin, forms.Form):
    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)
    email = forms.EmailField(label="Adresse e-mail", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(label="Téléphone", max_length=20, widget=forms.TextInput(attrs={"type": "tel"}))
    password1 = forms.CharField(
        label="Mot de passe",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="8 caractères minimum, pas uniquement des chiffres.",
    )
    password2 = forms.CharField(
        label="Confirmer le mot de passe",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    accept_terms = forms.BooleanField(
        label="J'accepte les conditions d'utilisation et la politique de confidentialité",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(username=email).exists() or User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cette adresse e-mail.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Les deux mots de passe ne correspondent pas.")
        elif p1:
            candidate = User(
                username=cleaned.get("email", ""),
                email=cleaned.get("email", ""),
                first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
            )
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return cleaned

    def create_user(self):
        data = self.cleaned_data
        return User.objects.create_user(
            username=data["email"],
            email=data["email"],
            password=data["password1"],
            first_name=data["first_name"],
            last_name=data["last_name"],
        )


class PatientSignupForm(BaseSignupForm):
    date_of_birth = forms.DateField(
        label="Date de naissance", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    gender = forms.ChoiceField(
        label="Genre", required=False, choices=[("", "Sélectionner")] + PatientProfile.Gender.choices
    )

    field_order = [
        "first_name", "last_name", "email", "phone", "date_of_birth", "gender",
        "password1", "password2", "accept_terms",
    ]

    @transaction.atomic
    def save(self):
        user = self.create_user()
        PatientProfile.objects.create(
            user=user,
            phone=self.cleaned_data["phone"],
            date_of_birth=self.cleaned_data.get("date_of_birth"),
            gender=self.cleaned_data.get("gender", ""),
        )
        return user


class DoctorSignupForm(BaseSignupForm):
    specialty = forms.ChoiceField(
        label="Spécialité", choices=[("", "Sélectionner")] + DoctorProfile.Specialty.choices
    )
    license_number = forms.CharField(label="Numéro d'inscription à l'Ordre", max_length=30)
    years_experience = forms.IntegerField(label="Années d'expérience", min_value=0, max_value=70, initial=0)
    city = forms.CharField(label="Ville d'exercice", max_length=100)
    practice_address = forms.CharField(label="Adresse du cabinet", max_length=255, required=False)
    bio = forms.CharField(
        label="Présentation (facultatif)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Votre parcours, vos domaines d'expertise…"}),
    )
    diploma = forms.FileField(label="Diplôme de médecine")
    license_document = forms.FileField(label="Attestation d'inscription à l'Ordre")
    certify = forms.BooleanField(
        label="Je certifie l'exactitude des informations et l'authenticité des documents fournis",
    )

    ALLOWED_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png")

    def clean_license_number(self):
        number = self.cleaned_data["license_number"].strip().upper()
        if DoctorProfile.objects.filter(license_number=number).exists():
            raise forms.ValidationError("Ce numéro est déjà associé à un compte.")
        return number

    def _clean_document(self, name):
        document = self.cleaned_data[name]
        if not document.name.lower().endswith(self.ALLOWED_EXTENSIONS):
            raise forms.ValidationError("Formats acceptés : PDF, JPG ou PNG.")
        if document.size > settings.MAX_UPLOAD_SIZE:
            raise forms.ValidationError("Le fichier ne doit pas dépasser 5 Mo.")
        return document

    def clean_diploma(self):
        return self._clean_document("diploma")

    def clean_license_document(self):
        return self._clean_document("license_document")


    @transaction.atomic
    def save(self):
        user = self.create_user()
        data = self.cleaned_data
        DoctorProfile.objects.create(
            user=user,
            phone=data["phone"],
            specialty=data["specialty"],
            license_number=data["license_number"],
            years_experience=data["years_experience"],
            city=data["city"],
            practice_address=data.get("practice_address", ""),
            bio=data.get("bio", ""),
            diploma=data["diploma"],
            license_document=data["license_document"],
        )
        return user


# --------------------------------------------------------------------------
# Account management (profile and settings)
# --------------------------------------------------------------------------

class UserNamesMixin(forms.Form):
    """First / last name live on the User; the rest on the profile."""

    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name

    @transaction.atomic
    def save(self, commit=True):
        user = self.instance.user
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.save(update_fields=["first_name", "last_name"])
        return super().save(commit)


class PatientProfileForm(StyledFormMixin, UserNamesMixin, forms.ModelForm):
    class Meta:
        model = PatientProfile
        fields = ["phone", "date_of_birth", "gender"]
        widgets = {
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}),
            "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    field_order = ["first_name", "last_name", "phone", "date_of_birth", "gender"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gender"].choices = [("", "Sélectionner")] + list(PatientProfile.Gender.choices)

    def clean_date_of_birth(self):
        value = self.cleaned_data.get("date_of_birth")
        if value:
            from datetime import date

            today = date.today()
            if value > today:
                raise forms.ValidationError("La date de naissance ne peut pas être dans le futur.")
            if today.year - value.year > 120:
                raise forms.ValidationError("Vérifiez l'année de naissance.")
        return value


class DoctorProfileForm(StyledFormMixin, UserNamesMixin, forms.ModelForm):
    """Specialty and licence number are checked by the back-office: not editable here."""

    class Meta:
        model = DoctorProfile
        fields = ["phone", "years_experience", "city", "practice_address", "bio"]
        widgets = {
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}),
            "bio": forms.Textarea(attrs={"rows": 5, "placeholder": "Votre parcours, vos domaines d'expertise, votre façon de travailler…"}),
        }
        help_texts = {"bio": "Visible par les patients quand ils choisissent un médecin."}

    field_order = ["first_name", "last_name", "phone", "years_experience", "city", "practice_address", "bio"]

    def clean_years_experience(self):
        value = self.cleaned_data["years_experience"]
        if value > 70:
            raise forms.ValidationError("Vérifiez le nombre d'années.")
        return value


class EmailChangeForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Nouvelle adresse e-mail", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        label="Mot de passe actuel", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="Pour confirmer que c'est bien vous.",
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if email == self.user.email.lower():
            raise forms.ValidationError("C'est déjà votre adresse actuelle.")
        taken = User.objects.exclude(pk=self.user.pk).filter(username=email).exists() or \
            User.objects.exclude(pk=self.user.pk).filter(email__iexact=email).exists()
        if taken:
            raise forms.ValidationError("Un compte existe déjà avec cette adresse e-mail.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Mot de passe incorrect.")
        return password

    def save(self):
        email = self.cleaned_data["email"]
        self.user.email = email
        self.user.username = email  # the e-mail is the login
        self.user.save(update_fields=["email", "username"])
        return self.user


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    error_messages = {
        **PasswordChangeForm.error_messages,
        "password_incorrect": "Mot de passe actuel incorrect.",
        "password_mismatch": "Les deux mots de passe ne correspondent pas.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)
        self.fields["new_password1"].help_text = ""  # rules are summed up above the form


class StyledPasswordResetForm(StyledFormMixin, PasswordResetForm):
    email = forms.EmailField(
        label="Adresse e-mail",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "nom@exemple.com"}),
    )


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    pass


class DoctorDecisionForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(choices=[("approve", "Approuver"), ("reject", "Refuser")])
    reason = forms.CharField(
        label="Motif du refus",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Expliquez au médecin pourquoi son dossier est refusé…"}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "reject" and not cleaned.get("reason", "").strip():
            self.add_error("reason", "Indiquez un motif pour refuser le dossier.")
        return cleaned
