from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.contrib.staticfiles.finders import find

from core.models import DoctorProfile, PatientProfile

User = get_user_model()
PASSWORD = "Handsfree-test-2026!"


class HandsfreeTemplateTests(TestCase):
    def setUp(self):
        self.patient = User.objects.create_user(
            "patient@handsfree.test", "patient@handsfree.test", PASSWORD
        )
        PatientProfile.objects.create(
            user=self.patient, phone="0600000000", date_of_birth=date(1990, 1, 1)
        )
        self.doctor = User.objects.create_user(
            "doctor@handsfree.test", "doctor@handsfree.test", PASSWORD
        )
        DoctorProfile.objects.create(
            user=self.doctor,
            phone="0600000001",
            specialty=DoctorProfile.Specialty.GENERAL,
            license_number="ORD-HF-1",
            city="Tunis",
            diploma="demo-diploma.pdf",
            license_document="demo-license.pdf",
        )
        self.admin = User.objects.create_user(
            "admin@handsfree.test", "admin@handsfree.test", PASSWORD, is_staff=True
        )

    def test_fieldset_renders_on_public_patient_doctor_and_admin_pages(self):
        cases = [
            ("public", None, reverse("home")),
            ("patient", self.patient, reverse("patient_dashboard")),
            ("doctor", self.doctor, reverse("doctor_dashboard")),
            ("admin", self.admin, reverse("backoffice:overview")),
        ]
        for label, user, url in cases:
            with self.subTest(page=label):
                self.client.logout()
                if user:
                    self.client.force_login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Navigation mains libres")
                self.assertContains(response, "Alt+Maj+G")

    def test_disabled_setting_hides_the_accessibility_fieldset_and_assets(self):
        with self.settings(HANDSFREE_ENABLED=False):
            response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Navigation mains libres")
        self.assertNotContains(response, "handsfree.js")

    def test_external_tracking_sdks_are_not_in_rendered_html(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "webgazer")
        self.assertNotContains(response, "mediapipe")
        self.assertNotContains(response, "storage.googleapis.com")

    def test_eye_tracking_uses_mediapipe_face_landmarks_not_webgazer(self):
        script = Path(__file__).parent / "static" / "handsfree" / "handsfree.js"
        source = script.read_text()
        self.assertIn("FaceLandmarker.createFromOptions", source)
        self.assertIn("LEFT_IRIS", source)
        self.assertIn("eyeBlinkLeft", source)
        self.assertNotIn("webgazer", source.lower())

    def test_handsfree_static_files_resolve(self):
        for path in (
            "handsfree/handsfree.js",
            "handsfree/handsfree-core.js",
            "handsfree/handsfree.css",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(find(path))

    def test_mode_choices_consent_and_calibration_dialogs_are_available(self):
        response = self.client.get(reverse("home"))
        for label in (
            "value=\"off\"",
            "value=\"eyes\"",
            "value=\"hands\"",
            "value=\"combined\"",
            "Autorisation caméra",
            "Calibration du clignement",
            "Aucune image n'est enregistrée ni envoyée",
        ):
            with self.subTest(content=label):
                self.assertContains(response, label)
