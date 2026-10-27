"""Who may open a medical record, and the access journal."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from appointments.models import Appointment

from .models import AccessLog

# A doctor follows a patient once they accepted (or completed) an appointment with them.
CARE_STATUSES = [Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED]
VIEW_LOG_WINDOW = timedelta(minutes=10)  # one "view" entry per doctor per 10 minutes
READ_ACTIONS = {AccessLog.Action.VIEW, AccessLog.Action.PRESCRIPTION_VIEW, AccessLog.Action.DOCUMENT_VIEW}


def doctor_follows(doctor, patient):
    if not hasattr(doctor, "doctor_profile") or not doctor.doctor_profile.is_verified:
        return False
    return Appointment.objects.filter(doctor=doctor, patient=patient, status__in=CARE_STATUSES).exists()


def patients_of(doctor):
    User = get_user_model()
    return (
        User.objects.filter(
            patient_profile__isnull=False,
            patient_appointments__doctor=doctor,
            patient_appointments__status__in=CARE_STATUSES,
        )
        .select_related("patient_profile")
        .distinct()
        .order_by("last_name", "first_name")
    )


def can_view(user, record):
    return user.pk == record.patient_id or doctor_follows(user, record.patient)


def client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


def log(request, record, action, detail=""):
    """Record an access. A patient reading their own file is not journaled."""
    user = request.user
    if user.pk == record.patient_id and action in READ_ACTIONS:
        return None
    if action == AccessLog.Action.VIEW:
        recent = AccessLog.objects.filter(
            record=record, user=user, action=action, created_at__gte=timezone.now() - VIEW_LOG_WINDOW
        )
        if recent.exists():
            return None
    return AccessLog.objects.create(
        record=record, user=user, action=action, detail=detail[:200], ip_address=client_ip(request)
    )
