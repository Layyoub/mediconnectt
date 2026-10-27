"""Profile and account settings for patients, doctors (and staff for the settings page)."""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import redirect, render

from .forms import DoctorProfileForm, EmailChangeForm, PatientProfileForm, StyledPasswordChangeForm
from .models import get_role

LAYOUTS = {
    "patient": "core/patient/_layout.html",
    "doctor": "core/doctor/_layout.html",
    "admin": "core/backoffice/_layout.html",
}


def _context(request, role, active_nav):
    ctx = {"role": role, "layout": LAYOUTS[role], "active_nav": active_nav}
    if role == "doctor":
        ctx["profile"] = request.user.doctor_profile
    return ctx


@login_required
def profile(request):
    role = get_role(request.user)
    if role == "patient":
        form_class, instance = PatientProfileForm, request.user.patient_profile
    elif role == "doctor":
        form_class, instance = DoctorProfileForm, request.user.doctor_profile
    else:
        return redirect("account_settings")

    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Votre profil a été mis à jour.")
        return redirect("account_profile")
    context = {**_context(request, role, "profile"), "form": form, "instance": instance}
    return render(request, "core/account/profile.html", context)


@login_required
def settings_view(request):
    role = get_role(request.user)
    if role is None:
        return redirect("home")
    action = request.POST.get("action") if request.method == "POST" else None
    email_form = EmailChangeForm(request.user, request.POST if action == "email" else None)
    password_form = StyledPasswordChangeForm(request.user, request.POST if action == "password" else None)

    if action == "email" and email_form.is_valid():
        old_email = request.user.email
        email_form.save()
        # Warn the previous address, in case the change was not made by the owner.
        send_mail(
            "MediConnect : votre adresse e-mail a été modifiée",
            f"Bonjour,\n\nL'adresse de connexion de votre compte MediConnect est désormais {request.user.email}.\n"
            "Si vous n'êtes pas à l'origine de ce changement, contactez-nous immédiatement.\n",
            None, [old_email], fail_silently=True,
        )
        messages.success(request, "Adresse e-mail modifiée. Utilisez-la désormais pour vous connecter.")
        return redirect("account_settings")

    if action == "password" and password_form.is_valid():
        user = password_form.save()
        update_session_auth_hash(request, user)  # stay logged in
        messages.success(request, "Mot de passe modifié.")
        return redirect("account_settings")

    context = {**_context(request, role, "settings"), "email_form": email_form, "password_form": password_form}
    return render(request, "core/account/settings.html", context)
