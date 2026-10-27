from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from appointments.models import Appointment
from core.models import DoctorProfile, PatientProfile
from records.models import MedicalRecord

User = get_user_model()
DEMO_PASSWORD = "MediConnect2026!"


class Command(BaseCommand):
    help = "Crée les dossiers de démonstration Pharma-Check (données cliniques fictives)."

    def handle(self, *args, **options):
        doctor, created = User.objects.get_or_create(
            username="pharmacheck@mediconnect.demo",
            defaults={
                "email": "pharmacheck@mediconnect.demo",
                "first_name": "Médecin",
                "last_name": "Démo Pharma-Check",
            },
        )
        if created:
            doctor.set_password(DEMO_PASSWORD)
            doctor.save()
        if not hasattr(doctor, "doctor_profile"):
            DoctorProfile.objects.create(
                user=doctor,
                phone="0000000000",
                specialty=DoctorProfile.Specialty.GENERAL,
                license_number="ORD-PHARMACHECK-DEMO",
                city="Ville Démo",
                status=DoctorProfile.Status.VERIFIED,
            )

        today = timezone.localdate()
        patients = [
            ("pharmacheck-warfarine@mediconnect.demo", "Patient", "Démo A", today.replace(year=today.year - 40),
             {"current_treatments": "Warfarine"}),
            ("pharmacheck-penicilline@mediconnect.demo", "Patient", "Démo B", today.replace(year=today.year - 30),
             {"allergies": "Allergie à la pénicilline"}),
            ("pharmacheck-enfant@mediconnect.demo", "Patient", "Démo C", today.replace(year=today.year - 8),
             {}),
        ]
        for index, (email, first, last, birth_date, record_values) in enumerate(patients):
            patient, created = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "first_name": first, "last_name": last},
            )
            if created:
                patient.set_password(DEMO_PASSWORD)
                patient.save()
            PatientProfile.objects.update_or_create(
                user=patient,
                defaults={"phone": "0000000000", "date_of_birth": birth_date},
            )
            record_defaults = {"weight_kg": 25 if index == 2 else None, **record_values}
            MedicalRecord.objects.update_or_create(patient=patient, defaults=record_defaults)
            start = timezone.now() + timedelta(days=2 + index)
            Appointment.objects.get_or_create(
                patient=patient,
                doctor=doctor,
                start=start,
                defaults={
                    "end": start + timedelta(minutes=30),
                    "reason": "Consultation de démonstration Pharma-Check",
                    "status": Appointment.Status.CONFIRMED,
                },
            )

        if not doctor.doctor_profile.is_verified:
            raise CommandError("Le compte médecin Pharma-Check doit être vérifié pour accéder aux dossiers.")
        self.stdout.write(self.style.SUCCESS(
            "Dossiers de démonstration créés : patient A (warfarine), patient B (allergie pénicilline), "
            "patient C (8 ans, 25 kg). Compte médecin : pharmacheck@mediconnect.demo."
        ))
