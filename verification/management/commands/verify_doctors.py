from django.core.management.base import BaseCommand

from core.models import DoctorProfile
from verification.models import DoctorVerification
from verification.services import analyse


class Command(BaseCommand):
    help = "Analyse les dossiers médecins (contrôles automatiques + agent IA)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Tous les médecins (par défaut : en attente uniquement).")

    def handle(self, *args, **options):
        doctors = DoctorProfile.objects.select_related("user")
        if not options["all"]:
            doctors = doctors.filter(status=DoctorProfile.Status.PENDING)
        for doctor in doctors:
            v = analyse(DoctorVerification.objects.create(doctor=doctor))
            ai = "IA" if v.ai_used else f"sans IA {v.error}".strip()
            self.stdout.write(f"{doctor} : {v.get_risk_display() or v.get_status_display()} ({ai})")
