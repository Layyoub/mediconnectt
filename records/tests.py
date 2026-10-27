import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from appointments.tests import PASSWORD, make_doctor, make_patient
from core.models import DoctorProfile
from teleconsult.models import Notification

from .models import AccessLog, ConsultationNote, Drug, MedicalDocument, MedicalRecord, Prescription

PDF = b"%PDF-1.4 test document"
TEMP_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class RecordTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.other_doctor = make_doctor("autre@test.fr", number="ORD-2")
        self.appointment = self._appointment(self.doctor, Appointment.Status.CONFIRMED)
        self.record = MedicalRecord.for_patient(self.patient)
        self.paracetamol = Drug.objects.get(name="Paracétamol", strength="1 g")
        self.amoxicilline = Drug.objects.get(name="Amoxicilline", strength="1 g")

    def _appointment(self, doctor, status, patient=None, days=2):
        start = timezone.now() + timedelta(days=days)
        return Appointment.objects.create(
            patient=patient or self.patient, doctor=doctor, start=start, end=start + timedelta(minutes=30),
            reason="Toux", status=status,
        )

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)

    def record_url(self, patient=None):
        return reverse("records:patient_record", args=[(patient or self.patient).pk])

    def prescription_data(self, drugs=None, **extra):
        drugs = drugs or [self.paracetamol]
        data = {
            "items-TOTAL_FORMS": str(len(drugs)), "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "10",
            "appointment": str(self.appointment.pk), "notes": "Boire beaucoup.",
            "valid_until": (timezone.localdate() + timedelta(days=90)).isoformat(),
        }
        for i, drug in enumerate(drugs):
            data.update({
                f"items-{i}-drug_name": drug if isinstance(drug, str) else drug.label,
                f"items-{i}-dosage": "1 comprimé",
                f"items-{i}-frequency": "3 fois par jour", f"items-{i}-duration": "5 jours",
            })
        data.update(extra)
        return data


class AccessTests(RecordTestCase):
    def test_doctor_without_appointment_gets_404(self):
        self.login(self.other_doctor)
        self.assertEqual(self.client.get(self.record_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse("records:new_prescription", args=[self.patient.pk])).status_code, 404)

    def test_pending_request_is_not_enough(self):
        self._appointment(self.other_doctor, Appointment.Status.PENDING)
        self.login(self.other_doctor)
        self.assertEqual(self.client.get(self.record_url()).status_code, 404)

    def test_unverified_doctor_is_redirected(self):
        DoctorProfile.objects.filter(user=self.doctor).update(status=DoctorProfile.Status.PENDING)
        self.login(self.doctor)
        self.assertRedirects(self.client.get(self.record_url()), reverse("doctor_dashboard"))

    def test_doctor_view_is_logged_once_and_shown_to_patient(self):
        self.login(self.doctor)
        self.assertContains(self.client.get(self.record_url()), "Amina Belhadj")
        self.client.get(self.record_url() + "?onglet=notes")
        self.assertEqual(AccessLog.objects.filter(action="view", user=self.doctor).count(), 1)

        self.login(self.patient)
        response = self.client.get(reverse("records:my_record") + "?onglet=acces")
        self.assertContains(response, "Dr Sarah Benali")
        self.assertContains(response, "Consultation du dossier")

    def test_patient_own_reads_are_not_logged(self):
        self.login(self.patient)
        self.client.get(reverse("records:my_record"))
        self.assertFalse(AccessLog.objects.exists())

    def test_doctor_has_no_access_tab(self):
        self.login(self.doctor)
        response = self.client.get(self.record_url() + "?onglet=acces")
        self.assertEqual(response.context["tab"], "apercu")

    def test_patient_list_shows_only_followed_patients(self):
        stranger = make_patient("autre.patient@test.fr", "Yasmine", "Trabelsi")
        self._appointment(self.doctor, Appointment.Status.REFUSED, patient=stranger, days=3)
        self.login(self.doctor)
        response = self.client.get(reverse("records:patients"))
        self.assertContains(response, "Amina Belhadj")
        self.assertNotContains(response, "Yasmine")


class RecordEditTests(RecordTestCase):
    def test_patient_updates_own_record(self):
        self.login(self.patient)
        response = self.client.post(reverse("records:edit_my_record"), {
            "blood_type": "A+", "height_cm": "165", "weight_kg": "60.5", "allergies": "Pénicilline",
        })
        self.assertRedirects(response, reverse("records:my_record"))
        self.record.refresh_from_db()
        self.assertEqual(self.record.allergies, "Pénicilline")
        self.assertEqual(self.record.bmi, 22.2)
        self.assertEqual(AccessLog.objects.get().action, "update")

    def test_doctor_update_notifies_patient(self):
        self.login(self.doctor)
        self.client.post(reverse("records:doctor_edit_record", args=[self.patient.pk]), {"chronic_conditions": "Asthme"})
        self.record.refresh_from_db()
        self.assertEqual(self.record.chronic_conditions, "Asthme")
        self.assertEqual(self.record.updated_by, self.doctor)
        self.assertTrue(Notification.objects.filter(user=self.patient, kind="record").exists())

    def test_private_note_hidden_from_patient(self):
        self.login(self.doctor)
        url = reverse("records:add_note", args=[self.patient.pk])
        self.client.post(url, {"title": "Note partagée", "body": "Va mieux.", "visible_to_patient": "on"})
        self.client.post(url, {"title": "Note privée", "body": "À surveiller."})
        self.assertEqual(ConsultationNote.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(user=self.patient, kind="record").count(), 1)

        self.login(self.patient)
        response = self.client.get(reverse("records:my_record") + "?onglet=notes")
        self.assertContains(response, "Note partagée")
        self.assertNotContains(response, "Note privée")


class PrescriptionTests(RecordTestCase):
    def url(self):
        return reverse("records:new_prescription", args=[self.patient.pk])

    def test_doctor_writes_prescription(self):
        self.login(self.doctor)
        response = self.client.post(self.url(), self.prescription_data([self.paracetamol, self.amoxicilline]))
        prescription = Prescription.objects.get()
        self.assertRedirects(response, reverse("records:prescription", args=[prescription.code]))
        self.assertEqual(prescription.items.count(), 2)
        self.assertEqual(prescription.appointment, self.appointment)
        self.assertEqual(prescription.doctor, self.doctor)
        self.assertTrue(prescription.is_valid)
        self.assertTrue(Notification.objects.filter(user=self.patient, kind="prescription").exists())
        self.assertTrue(AccessLog.objects.filter(action="prescription").exists())

        # The patient reads and prints it; it shows up on the dashboard.
        self.login(self.patient)
        self.assertContains(self.client.get(reverse("records:prescription", args=[prescription.code])), "3 fois par jour")
        self.assertContains(self.client.get(reverse("patient_dashboard")), prescription.code)
        self.assertContains(self.client.get(reverse("records:prescriptions")), "Paracétamol")

    def test_allergy_requires_documented_override_reason(self):
        self.record.allergies = "Allergie aux pénicillines"
        self.record.save()
        self.login(self.doctor)
        response = self.client.post(self.url(), self.prescription_data([self.amoxicilline]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "allergie possible")
        self.assertFalse(Prescription.objects.exists())

        self.client.post(
            self.url(),
            self.prescription_data(
                [self.amoxicilline], override_reason="Bénéfice clinique évalué par le médecin."
            ),
        )
        self.assertTrue(Prescription.objects.exists())

    def test_no_allergy_warning_for_unrelated_drug(self):
        self.record.allergies = "Pénicilline"
        self.record.save()
        self.login(self.doctor)
        self.client.post(self.url(), self.prescription_data([self.paracetamol]))
        self.assertTrue(Prescription.objects.exists())

    def test_free_text_drug_is_accepted_and_linked_when_known(self):
        self.login(self.doctor)
        self.client.post(self.url(), self.prescription_data(["Doliprane 1000 mg sachet", "amoxicilline 750 mg"]))
        unknown, known = Prescription.objects.get().items.order_by("id")
        self.assertEqual(unknown.drug_name, "Doliprane 1000 mg sachet")
        self.assertIsNone(unknown.drug)
        self.assertEqual(known.drug.name, "Amoxicilline")

    def test_allergy_check_on_typed_name(self):
        self.record.allergies = "Pénicilline ; Doliprane"
        self.record.save()
        self.login(self.doctor)
        # Not in the catalogue, but the typed word is in the allergies.
        response = self.client.post(self.url(), self.prescription_data(["Doliprane 1000 mg"]))
        self.assertContains(response, "allergie possible")
        # Typed with another dose: matched to the catalogue, class "pénicillines" detected.
        response = self.client.post(self.url(), self.prescription_data(["Amoxicilline 750 mg"]))
        self.assertContains(response, "pénicillines")
        self.assertFalse(Prescription.objects.exists())

    def test_invalid_prescriptions_rejected(self):
        self.login(self.doctor)
        response = self.client.post(self.url(), self.prescription_data([self.paracetamol, self.paracetamol]))
        self.assertContains(response, "figure deux fois")
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.client.post(self.url(), self.prescription_data(valid_until=past))
        self.assertContains(response, "déjà passée")
        self.assertFalse(Prescription.objects.exists())

    def test_other_users_cannot_read_prescription(self):
        self.login(self.doctor)
        self.client.post(self.url(), self.prescription_data())
        code = Prescription.objects.get().code
        make_patient("intrus@test.fr")
        self.client.login(username="intrus@test.fr", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("records:prescription", args=[code])).status_code, 404)
        self.login(self.other_doctor)
        self.assertEqual(self.client.get(reverse("records:prescription", args=[code])).status_code, 404)


class DocumentTests(RecordTestCase):
    def upload(self, url, content=PDF, name="bilan.pdf"):
        return self.client.post(url, {
            "title": "Bilan sanguin", "kind": "analysis",
            "file": SimpleUploadedFile(name, content, content_type="application/pdf"),
        })

    def test_patient_uploads_and_doctor_opens(self):
        self.login(self.patient)
        response = self.upload(reverse("records:add_my_document"))
        self.assertRedirects(response, reverse("records:my_record") + "?onglet=documents")
        document = MedicalDocument.objects.get()
        self.assertEqual(document.original_name, "bilan.pdf")
        self.assertEqual(document.size, len(PDF))

        self.login(self.doctor)
        response = self.client.get(reverse("records:document", args=[document.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertTrue(AccessLog.objects.filter(action="document_view", user=self.doctor).exists())

        self.login(self.other_doctor)
        self.assertEqual(self.client.get(reverse("records:document", args=[document.pk])).status_code, 404)

    def test_fake_file_rejected(self):
        self.login(self.patient)
        response = self.upload(reverse("records:add_my_document"), content=b"MZ not a pdf")
        self.assertContains(response, "ne correspond pas")
        self.assertFalse(MedicalDocument.objects.exists())

    def test_only_uploader_can_delete(self):
        self.login(self.patient)
        self.upload(reverse("records:add_my_document"))
        document = MedicalDocument.objects.get()
        self.login(self.doctor)
        self.assertEqual(self.client.post(reverse("records:document_delete", args=[document.pk])).status_code, 404)
        self.login(self.patient)
        self.client.post(reverse("records:document_delete", args=[document.pk]))
        self.assertFalse(MedicalDocument.objects.exists())

    def test_doctor_upload_notifies_patient(self):
        self.login(self.doctor)
        self.upload(reverse("records:doctor_add_document", args=[self.patient.pk]), content=b"\x89PNG\r\n\x1a\n", name="radio.png")
        self.assertTrue(MedicalDocument.objects.filter(uploaded_by=self.doctor).exists())
        self.assertTrue(Notification.objects.filter(user=self.patient, kind="record").exists())
