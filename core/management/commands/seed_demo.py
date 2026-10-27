from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from appointments.models import Appointment, Availability, Specialty
from followups.models import FollowUpPlan, FollowUpTask
from records.models import (
    AccessLog,
    ConsultationNote,
    Drug,
    MedicalDocument,
    MedicalRecord,
    Prescription,
    PrescriptionItem,
)
from teleconsult.models import ChatMessage, ConsultationRoom, ConsultationSummary
from teleconsult.services import room_for

from core.models import DoctorProfile, PatientProfile
from triage.engine import complete_session
from triage.models import SymptomEntry, TriageSession

User = get_user_model()
PASSWORD = "MediConnect2026!"


class Command(BaseCommand):
    help = "Crée des comptes de démonstration (admin, patient, médecins)."

    def handle(self, *args, **options):
        def user(email, first, last, **extra):
            u, created = User.objects.get_or_create(
                username=email, defaults={"email": email, "first_name": first, "last_name": last, **extra}
            )
            if created:
                u.set_password(PASSWORD)
                u.save()
            return u

        user("admin@mediconnect.demo", "Admin", "MediConnect", is_staff=True, is_superuser=True)

        patient = user("patient@mediconnect.demo", "Amina", "Belhadj")
        PatientProfile.objects.get_or_create(
            user=patient, defaults={"phone": "+00 6 12 34 56 78", "date_of_birth": date(1992, 3, 14)}
        )
        if not patient.triage_sessions.exists():
            session = TriageSession.objects.create(
                patient=patient,
                chief_complaint="J'ai de la fièvre, je tousse et je suis très fatiguée depuis trois jours.",
                temperature=Decimal("38.5"),
                age=TriageSession.age_from_birth_date(date(1992, 3, 14)),
            )
            for code in ("fever", "cough", "fatigue"):
                SymptomEntry.objects.create(session=session, symptom=code, severity=2, duration="1-3d")
            complete_session(session)

        doctors = [
            ("medecin@mediconnect.demo", "Sarah", "Benali", "general", "ORD-10001", DoctorProfile.Status.VERIFIED),
            ("attente@mediconnect.demo", "Karim", "Haddad", "pulmonology", "ORD-10002", DoctorProfile.Status.PENDING),
            ("leila@mediconnect.demo", "Leïla", "Mansour", "dermatology", "ORD-10003", DoctorProfile.Status.PENDING),
            ("yanis@mediconnect.demo", "Yanis", "Cherif", "general", "ORD-10004", DoctorProfile.Status.VERIFIED),
            ("nour@mediconnect.demo", "Nour", "Ziani", "cardiology", "ORD-10005", DoctorProfile.Status.VERIFIED),
        ]
        for email, first, last, specialty, number, status in doctors:
            u = user(email, first, last)
            if not hasattr(u, "doctor_profile"):
                doc = DoctorProfile(
                    user=u, phone="+00 6 00 00 00 00", specialty=specialty, license_number=number,
                    years_experience=8, city="Ville Démo", status=status,
                )
                for field in ("diploma", "license_document"):
                    getattr(doc, field).save(f"{number}-{field}.pdf", ContentFile(b"%PDF-1.4 demo"), save=False)
                doc.save()

        self._seed_appointments(patient)
        self._seed_record(patient)
        self._seed_followup(patient)

        self.stdout.write(self.style.SUCCESS(
            f"Comptes de démonstration prêts (mot de passe : {PASSWORD})\n"
            "  admin@mediconnect.demo    -> back-office\n"
            "  patient@mediconnect.demo  -> espace patient\n"
            "  medecin@mediconnect.demo  -> espace médecin (vérifié)\n"
            "  attente@mediconnect.demo  -> espace médecin (en attente)\n"
            "  yanis@ / nour@mediconnect.demo -> autres médecins vérifiés"
        ))

    def _seed_appointments(self, patient):
        """Weekly availabilities for the verified doctors and two sample appointments."""
        week = {
            "medecin@mediconnect.demo": [(d, time(9), time(12)) for d in range(5)] + [(d, time(14), time(17)) for d in (0, 2, 4)],
            "yanis@mediconnect.demo": [(d, time(13), time(18)) for d in range(6)],
            "nour@mediconnect.demo": [(d, time(10), time(13)) for d in (1, 3)],
        }
        for email, ranges in week.items():
            doctor = User.objects.get(username=email)
            if doctor.availabilities.exists():
                continue
            for weekday, start, end in ranges:
                Availability.objects.create(doctor=doctor, weekday=weekday, start_time=start, end_time=end)

        self._seed_past_consultation(patient)
        if patient.patient_appointments.exclude(status=Appointment.Status.COMPLETED).exists():
            self._ensure_rooms()
            return
        # Next weekday (Mon-Fri) at least 2 days ahead.
        day = timezone.localdate() + timedelta(days=2)
        while day.weekday() > 4:
            day += timedelta(days=1)
        at = lambda d, h: timezone.make_aware(datetime.combine(d, time(h)))
        benali = User.objects.get(username="medecin@mediconnect.demo")
        cherif = User.objects.get(username="yanis@mediconnect.demo")
        general = Specialty.objects.filter(code="general").first()
        Appointment.objects.create(
            patient=patient, doctor=benali, specialty=general,
            triage_session=patient.triage_sessions.filter(status="completed").first(),
            start=at(day, 10), end=at(day, 10) + timedelta(minutes=30),
            reason="Fièvre, toux et grande fatigue depuis trois jours.",
        )
        Appointment.objects.create(
            patient=patient, doctor=cherif, specialty=general, status=Appointment.Status.CONFIRMED,
            start=at(day + timedelta(days=1), 15), end=at(day + timedelta(days=1), 15) + timedelta(minutes=30),
            reason="Renouvellement d'ordonnance et contrôle de la tension.",
            doctor_message="C'est noté, à bientôt en téléconsultation.", decided_at=timezone.now(),
        )
        self._ensure_rooms()

    def _ensure_rooms(self):
        for appointment in Appointment.objects.filter(status=Appointment.Status.CONFIRMED):
            room_for(appointment)

    def _seed_followup(self, patient):
        """An idempotent doctor/patient follow-up demo tied to the finished consultation."""
        appointment = Appointment.objects.filter(
            patient=patient, status=Appointment.Status.COMPLETED,
        ).order_by("-start").first()
        if not appointment:
            return

        plan, _ = FollowUpPlan.objects.get_or_create(
            appointment=appointment,
            defaults={
                "title": "Suivi après la téléconsultation",
                "instructions": (
                    "Suivez les consignes validées dans votre ordonnance. "
                    "Contactez votre médecin si vos symptômes s'aggravent ou si vous avez une question."
                ),
            },
        )
        today = timezone.localdate()
        tasks = [
            ("Noter l'évolution des symptômes", "Indiquez si la douleur et la fièvre s'améliorent.", 2,
             FollowUpTask.Status.IN_PROGRESS),
            ("Suivre les consignes de l'ordonnance", "Vérifiez les instructions approuvées par votre médecin.", 1,
             FollowUpTask.Status.TODO),
            ("Prévoir un rendez-vous de contrôle", "Recontactez le cabinet si les symptômes ne s'améliorent pas.", 0,
             FollowUpTask.Status.DONE),
        ]
        for title, details, days, status in tasks:
            FollowUpTask.objects.get_or_create(
                plan=plan,
                title=title,
                defaults={
                    "details": details,
                    "due_date": today + timedelta(days=days),
                    "status": status,
                },
            )

    def _seed_past_consultation(self, patient):
        """A finished teleconsultation with chat history and a report."""
        if ConsultationSummary.objects.filter(room__appointment__patient=patient).exists():
            return
        benali = User.objects.get(username="medecin@mediconnect.demo")
        day = timezone.localdate() - timedelta(days=12)
        start = timezone.make_aware(datetime.combine(day, time(11)))
        appointment = Appointment.objects.create(
            patient=patient, doctor=benali, specialty=Specialty.objects.filter(code="general").first(),
            status=Appointment.Status.COMPLETED, start=start, end=start + timedelta(minutes=30),
            reason="Mal de gorge et fièvre légère depuis deux jours.", decided_at=start - timedelta(days=1),
        )
        room = room_for(appointment)
        room.status = ConsultationRoom.Status.CLOSED
        room.opened_at = start
        room.closed_at = start + timedelta(minutes=18)
        room.save()
        lines = [
            (None, "Dr Sarah Benali a ouvert la salle de consultation.", 0),
            (benali, "Bonjour Madame Belhadj, je vous écoute. Depuis quand avez-vous mal à la gorge ?", 1),
            (patient, "Bonjour docteur, depuis deux jours. J'ai eu 38 °C hier soir.", 2),
            (benali, "Avez-vous du mal à avaler ou à respirer ?", 3),
            (patient, "Un peu de mal à avaler, mais je respire bien.", 4),
            (None, "La consultation est terminée. Le compte rendu est disponible.", 18),
        ]
        for sender, body, minute in lines:
            msg = ChatMessage.objects.create(
                room=room, sender=sender, body=body,
                kind=ChatMessage.Kind.TEXT if sender else ChatMessage.Kind.SYSTEM,
            )
            ChatMessage.objects.filter(pk=msg.pk).update(created_at=start + timedelta(minutes=minute))
        ConsultationSummary.objects.create(
            room=room, author=benali,
            diagnosis="Angine probablement virale, sans signe de gravité.",
            observations="Douleur pharyngée depuis 48 h, fièvre à 38 °C, pas de gêne respiratoire, déglutition un peu douloureuse.",
            recommendations="Repos, boissons chaudes et hydratation abondante. Paracétamol si fièvre ou douleur, en respectant la notice. "
                            "Reconsultez si la fièvre dépasse 39 °C, dure plus de 3 jours ou si vous avez du mal à respirer.",
            follow_up="Nouvelle téléconsultation si pas d'amélioration sous 5 jours.",
        )

    def _seed_record(self, patient):
        """Medical record of the demo patient: health info, a prescription, a note and a document."""
        record = MedicalRecord.for_patient(patient)
        if record.prescriptions.exists():
            return
        benali = User.objects.get(username="medecin@mediconnect.demo")
        MedicalRecord.objects.filter(pk=record.pk).update(
            blood_type="O+", height_cm=165, weight_kg=Decimal("60.5"),
            allergies="Pénicilline (urticaire en 2019)",
            chronic_conditions="Asthme léger",
            current_treatments="Salbutamol inhalé si besoin",
            medical_history="Appendicectomie en 2010",
            family_history="Diabète de type 2 (père)",
            emergency_contact="Karim Belhadj (frère) · +00 6 98 76 54 32",
            updated_by=patient,
        )
        past = Appointment.objects.filter(patient=patient, doctor=benali, status=Appointment.Status.COMPLETED).first()
        when = past.end if past else timezone.now()

        prescription = Prescription.objects.create(
            record=record, doctor=benali, appointment=past,
            notes="Boire beaucoup d'eau. Reconsultez si la fièvre dépasse 39 °C ou dure plus de 3 jours.",
            valid_until=timezone.localdate(when) + timedelta(days=90),
        )
        lines = [
            (("Paracétamol", "tablet", "1 g"), "1 comprimé", "toutes les 6 heures si fièvre ou douleur", "5 jours", "4 comprimés par jour au maximum"),
            (("Spray nasal à l'eau de mer", "spray", ""), "2 pulvérisations dans chaque narine", "3 fois par jour", "7 jours", ""),
        ]
        for (name, form, strength), dosage, frequency, duration, instructions in lines:
            drug = Drug.objects.get(name=name, form=form, strength=strength)
            PrescriptionItem.objects.create(
                prescription=prescription, drug=drug, drug_name=f"{drug.label} ({drug.get_form_display().lower()})",
                dosage=dosage, frequency=frequency, duration=duration, instructions=instructions,
            )
        note = ConsultationNote.objects.create(
            record=record, author=benali, appointment=past, title="Angine virale — suivi",
            body="Pas d'antibiotique (allergie à la pénicilline et origine probablement virale). "
                 "Traitement symptomatique. À revoir si pas d'amélioration sous 5 jours.",
        )
        document = MedicalDocument(
            record=record, uploaded_by=patient, kind=MedicalDocument.Kind.ANALYSIS,
            title="Bilan sanguin de contrôle", document_date=timezone.localdate() - timedelta(days=40),
            original_name="bilan-sanguin.pdf",
        )
        content = _demo_pdf("Bilan sanguin de contrôle - Amina Belhadj (document de démonstration)")
        document.file.save("bilan-sanguin.pdf", ContentFile(content), save=False)
        document.size = len(content)
        document.save()
        for obj in (prescription, note):
            type(obj).objects.filter(pk=obj.pk).update(created_at=when)
        AccessLog.objects.bulk_create([
            AccessLog(record=record, user=benali, action=AccessLog.Action.VIEW),
            AccessLog(record=record, user=benali, action=AccessLog.Action.NOTE, detail=note.title),
            AccessLog(record=record, user=benali, action=AccessLog.Action.PRESCRIPTION, detail=prescription.code),
        ])
        AccessLog.objects.filter(record=record).update(created_at=when)


def _demo_pdf(text):
    """A tiny one-page PDF with one line of text."""
    stream = f"BT /F1 14 Tf 60 780 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
