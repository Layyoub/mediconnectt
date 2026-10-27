from django.conf import settings
from django.urls import reverse

from .models import get_role

# Keyboard shortcuts (Alt + Maj + letter), per role. Letters are physical keys (work in AZERTY too).
SHORTCUTS = {
    "patient": [
        ("D", "Tableau de bord", "patient_dashboard"),
        ("R", "Rendez-vous", "appointments:list"),
        ("C", "Consultations", "teleconsult:list"),
        ("F", "Dossier médical", "records:my_record"),
        ("O", "Ordonnances", "records:prescriptions"),
        ("N", "Notifications", "teleconsult:notifications"),
    ],
    "doctor": [
        ("D", "Tableau de bord", "doctor_dashboard"),
        ("E", "Demandes", "appointments:requests"),
        ("R", "Agenda", "appointments:agenda"),
        ("C", "Consultations", "teleconsult:list"),
        ("P", "Mes patients", "records:patients"),
        ("N", "Notifications", "teleconsult:notifications"),
    ],
    "admin": [
        ("D", "Vue d'ensemble", "backoffice:overview"),
        ("P", "Médecins", "backoffice:doctor_list"),
        ("R", "Rendez-vous", "backoffice:appointments"),
        ("N", "Notifications", "teleconsult:notifications"),
    ],
}


def shortcuts_for(role):
    return [{"key": key, "label": label, "url": reverse(name)} for key, label, name in SHORTCUTS.get(role, [])]


def user_role(request):
    role = get_role(request.user)
    # Bumped when CSS/JS change, so browsers never keep an old version in cache.
    context = {
        "user_role": role,
        "asset_version": settings.ASSET_VERSION,
        "shortcuts": shortcuts_for(role),
        "handsfree_enabled": settings.HANDSFREE_ENABLED,
    }
    if request.user.is_authenticated:
        context["unread_notifications"] = request.user.notifications.filter(is_read=False).count()
    if role == "doctor" and request.user.doctor_profile.is_verified:
        from django.utils import timezone

        from appointments.models import Appointment

        context["doctor_pending_count"] = Appointment.objects.filter(
            doctor=request.user, status=Appointment.Status.PENDING, start__gt=timezone.now()
        ).count()
    return context
