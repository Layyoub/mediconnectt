from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import DoctorProfile, PatientProfile
from triage.engine import complete_session
from triage.models import SymptomEntry, TriageSession

from .forms import AvailabilityForm
from .models import Appointment, Availability, Specialty
from .slots import MIN_NOTICE, available_slots, next_slot

User = get_user_model()
PASSWORD = "Test-pass-2026!"


def make_patient(email="patient@test.fr", first="Amina", last="Belhadj"):
    user = User.objects.create_user(email, email, PASSWORD, first_name=first, last_name=last)
    PatientProfile.objects.create(user=user, phone="0600000000", date_of_birth=date(1990, 5, 1))
    return user


def make_doctor(email="doc@test.fr", specialty="general", status=DoctorProfile.Status.VERIFIED, number="ORD-1"):
    user = User.objects.create_user(email, email, PASSWORD, first_name="Sarah", last_name="Benali")
    doc = DoctorProfile(user=user, phone="0600000000", specialty=specialty, license_number=number,
                        city="Tunis", status=status)
    for field in ("diploma", "license_document"):
        getattr(doc, field).save(f"{number}-{field}.pdf", ContentFile(b"%PDF"), save=False)
    doc.save()
    return user


def open_every_day(doctor, start=time(0), end=time(23, 30)):
    for weekday in range(7):
        Availability.objects.create(doctor=doctor, weekday=weekday, start_time=start, end_time=end)


def iso(dt):
    return timezone.localtime(dt).isoformat()


class SpecialtySeedTests(TestCase):
    def test_specialties_are_seeded(self):
        self.assertEqual(Specialty.objects.count(), len(DoctorProfile.Specialty.choices))
        self.assertEqual(Specialty.objects.get(code="general").name, "Médecine générale")


class SlotTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()

    def test_no_availability_means_no_slot(self):
        self.assertEqual(available_slots(self.doctor), [])

    def test_slots_follow_weekly_rules(self):
        Availability.objects.create(doctor=self.doctor, weekday=0, start_time=time(9), end_time=time(11), slot_minutes=30)
        days = available_slots(self.doctor)
        self.assertTrue(days)
        for day in days:
            self.assertEqual(day.day.weekday(), 0)
            self.assertLessEqual(len(day.slots), 4)
            for start, end in day.slots:
                self.assertEqual(end - start, timedelta(minutes=30))
                self.assertGreaterEqual(start, timezone.now() + MIN_NOTICE)

    def test_taken_slots_are_hidden_but_refused_ones_are_free_again(self):
        open_every_day(self.doctor)
        start, end = next_slot(self.doctor)
        patient = make_patient()
        a = Appointment.objects.create(patient=patient, doctor=self.doctor, start=start, end=end, reason="Test")
        self.assertNotEqual(next_slot(self.doctor)[0], start)
        a.status = Appointment.Status.REFUSED
        a.save()
        self.assertEqual(next_slot(self.doctor)[0], start)

    def test_overlapping_availability_is_rejected(self):
        Availability.objects.create(doctor=self.doctor, weekday=1, start_time=time(9), end_time=time(12))
        form = AvailabilityForm({"weekday": 1, "start_time": "11:00", "end_time": "13:00", "slot_minutes": 30}, doctor=self.doctor)
        self.assertFalse(form.is_valid())
        form = AvailabilityForm({"weekday": 1, "start_time": "12:00", "end_time": "11:00", "slot_minutes": 30}, doctor=self.doctor)
        self.assertFalse(form.is_valid())
        form = AvailabilityForm({"weekday": 1, "start_time": "14:00", "end_time": "17:00", "slot_minutes": 30}, doctor=self.doctor)
        self.assertTrue(form.is_valid(), form.errors)


class BookingTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        open_every_day(self.doctor)
        self.profile = self.doctor.doctor_profile
        self.patient = make_patient()
        self.client.login(username="patient@test.fr", password=PASSWORD)

    def _triage(self):
        session = TriageSession.objects.create(
            patient=self.patient, chief_complaint="Fièvre et toux depuis 3 jours", temperature=Decimal("38.5"), age=35
        )
        SymptomEntry.objects.create(session=session, symptom="fever", severity=2, duration="1-3d")
        complete_session(session)
        return session

    def test_book_page_lists_verified_doctors_only(self):
        make_doctor("pending@test.fr", status=DoctorProfile.Status.PENDING, number="ORD-2")
        response = self.client.get(reverse("appointments:book"))
        self.assertContains(response, "Dr Sarah Benali")
        self.assertEqual(len(response.context["doctors"]), 1)

    def test_triage_preselects_specialty(self):
        make_doctor("cardio@test.fr", specialty="cardiology", number="ORD-3")
        session = self._triage()
        response = self.client.get(reverse("appointments:book"), {"triage": session.pk})
        self.assertEqual(response.context["selected"].code, session.result.suggested_specialty)
        self.assertTrue(all(d.specialty == session.result.suggested_specialty for d in response.context["doctors"]))

    def test_cannot_use_someone_elses_triage(self):
        other = make_patient("other@test.fr")
        session = TriageSession.objects.create(patient=other, chief_complaint="Mal de tête", status="completed")
        response = self.client.get(reverse("appointments:book"), {"triage": session.pk})
        self.assertIsNone(response.context["triage"])

    def test_booking_creates_pending_request_and_notifies_doctor(self):
        session = self._triage()
        start, end = next_slot(self.doctor)
        response = self.client.post(
            reverse("appointments:doctor_slots", args=[self.profile.pk]),
            {"slot": iso(start), "reason": "Fièvre et toux", "triage_session": session.pk},
        )
        appointment = Appointment.objects.get()
        self.assertRedirects(response, reverse("appointments:detail", args=[appointment.pk]))
        self.assertEqual(appointment.status, Appointment.Status.PENDING)
        self.assertEqual(appointment.start, start)
        self.assertEqual(appointment.end, end)
        self.assertEqual(appointment.triage_session, session)
        self.assertEqual(appointment.specialty.code, "general")
        self.assertEqual(mail.outbox[0].to, ["doc@test.fr"])

    def test_taken_or_invented_slot_is_refused(self):
        start, end = next_slot(self.doctor)
        other = make_patient("other@test.fr")
        Appointment.objects.create(patient=other, doctor=self.doctor, start=start, end=end, reason="x")
        url = reverse("appointments:doctor_slots", args=[self.profile.pk])
        response = self.client.post(url, {"slot": iso(start), "reason": "Fièvre et toux"})
        self.assertContains(response, "plus disponible")
        response = self.client.post(url, {"slot": iso(start + timedelta(minutes=7)), "reason": "Fièvre et toux"})
        self.assertContains(response, "plus disponible")
        response = self.client.post(url, {"slot": "pas-une-date", "reason": "Fièvre et toux"})
        self.assertContains(response, "Créneau invalide")
        self.assertEqual(Appointment.objects.count(), 1)

    def test_cannot_book_unverified_doctor(self):
        pending = make_doctor("pending@test.fr", status=DoctorProfile.Status.PENDING, number="ORD-2")
        response = self.client.get(reverse("appointments:doctor_slots", args=[pending.doctor_profile.pk]))
        self.assertEqual(response.status_code, 404)

    def test_patient_sees_and_cancels_own_appointment(self):
        start, end = next_slot(self.doctor)
        a = Appointment.objects.create(patient=self.patient, doctor=self.doctor, start=start, end=end, reason="x")
        self.assertContains(self.client.get(reverse("appointments:list")), "Dr Sarah Benali")
        self.client.post(reverse("appointments:cancel", args=[a.pk]), {"message": "Empêchement"})
        a.refresh_from_db()
        self.assertEqual(a.status, Appointment.Status.CANCELLED)
        self.assertEqual(a.cancelled_by, self.patient)
        self.assertEqual(a.cancel_reason, "Empêchement")

    def test_patient_cannot_see_others_appointment(self):
        other = make_patient("other@test.fr")
        start, end = next_slot(self.doctor)
        a = Appointment.objects.create(patient=other, doctor=self.doctor, start=start, end=end, reason="x")
        self.assertEqual(self.client.get(reverse("appointments:detail", args=[a.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("appointments:cancel", args=[a.pk])).status_code, 404)

    def test_patient_cannot_decide(self):
        start, end = next_slot(self.doctor)
        a = Appointment.objects.create(patient=self.patient, doctor=self.doctor, start=start, end=end, reason="x")
        self.client.post(reverse("appointments:decide", args=[a.pk]), {"decision": "accept"})
        a.refresh_from_db()
        self.assertEqual(a.status, Appointment.Status.PENDING)

    def test_dashboard_shows_real_appointment(self):
        start, end = next_slot(self.doctor)
        Appointment.objects.create(patient=self.patient, doctor=self.doctor, start=start, end=end, reason="x")
        response = self.client.get(reverse("patient_dashboard"))
        self.assertContains(response, "Dr Sarah Benali")
        self.assertContains(response, "En attente de validation")


class DoctorDecisionTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        open_every_day(self.doctor)
        self.patient = make_patient()
        start, end = next_slot(self.doctor)
        session = TriageSession.objects.create(patient=self.patient, chief_complaint="Toux sèche persistante", age=35)
        SymptomEntry.objects.create(session=session, symptom="cough", severity=2, duration="4-7d")
        complete_session(session)
        self.appointment = Appointment.objects.create(
            patient=self.patient, doctor=self.doctor, start=start, end=end, reason="Toux", triage_session=session
        )
        self.client.login(username="doc@test.fr", password=PASSWORD)

    def test_requests_page_and_detail_show_shared_triage(self):
        self.assertContains(self.client.get(reverse("appointments:requests")), "Amina Belhadj")
        response = self.client.get(reverse("appointments:detail", args=[self.appointment.pk]))
        self.assertContains(response, "Évaluation partagée par le patient")
        self.assertContains(response, "Toux sèche persistante")

    def test_accept(self):
        response = self.client.post(reverse("appointments:decide", args=[self.appointment.pk]), {"decision": "accept"})
        self.assertRedirects(response, reverse("appointments:requests"))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.CONFIRMED)
        self.assertIsNotNone(self.appointment.decided_at)
        self.assertEqual(mail.outbox[0].to, ["patient@test.fr"])
        self.assertContains(self.client.get(reverse("appointments:agenda")), "Amina Belhadj")

    def test_refuse_requires_message(self):
        url = reverse("appointments:decide", args=[self.appointment.pk])
        self.client.post(url, {"decision": "refuse", "message": "  "})
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.PENDING)
        self.client.post(url, {"decision": "refuse", "message": "Indisponible, merci de choisir un autre créneau."})
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.REFUSED)
        self.assertIn("Indisponible", mail.outbox[0].body)

    def test_cannot_decide_twice(self):
        url = reverse("appointments:decide", args=[self.appointment.pk])
        self.client.post(url, {"decision": "accept"})
        self.client.post(url, {"decision": "refuse", "message": "Finalement non"})
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.CONFIRMED)

    def test_next_redirect_must_be_local(self):
        response = self.client.post(
            reverse("appointments:decide", args=[self.appointment.pk]),
            {"decision": "accept", "next": "https://evil.example.com/"},
        )
        self.assertRedirects(response, reverse("appointments:requests"))

    def test_other_doctor_cannot_decide_or_view(self):
        make_doctor("other@test.fr", number="ORD-9")
        self.client.login(username="other@test.fr", password=PASSWORD)
        response = self.client.post(reverse("appointments:decide", args=[self.appointment.pk]), {"decision": "accept"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse("appointments:detail", args=[self.appointment.pk])).status_code, 404)

    def test_unverified_doctor_is_redirected(self):
        make_doctor("pending@test.fr", status=DoctorProfile.Status.PENDING, number="ORD-8")
        self.client.login(username="pending@test.fr", password=PASSWORD)
        self.assertRedirects(self.client.get(reverse("appointments:requests")), reverse("doctor_dashboard"))

    def test_dashboard_uses_real_data(self):
        response = self.client.get(reverse("doctor_dashboard"))
        self.assertContains(response, "Amina Belhadj")
        self.assertEqual(response.context["stats"]["pending"], 1)
        self.assertEqual(response.context["doctor_pending_count"], 1)

    def test_add_and_delete_availability(self):
        response = self.client.post(
            reverse("appointments:availability"),
            {"weekday": 2, "start_time": "08:00", "end_time": "08:00", "slot_minutes": 30},
        )
        self.assertEqual(response.status_code, 200)  # invalid: end == start
        rule = self.doctor.availabilities.first()
        self.client.post(reverse("appointments:delete_availability", args=[rule.pk]))
        self.assertFalse(Availability.objects.filter(pk=rule.pk).exists())
        self.assertEqual(Appointment.objects.count(), 1)  # booked appointments are kept


class BackofficeTests(TestCase):
    def test_staff_page(self):
        User.objects.create_user("admin@test.fr", "admin@test.fr", PASSWORD, is_staff=True)
        self.client.login(username="admin@test.fr", password=PASSWORD)
        response = self.client.get(reverse("backoffice:appointments"))
        self.assertContains(response, "Médecine générale")
