"""Server-side checks and back-office flow. The OpenAI agent is exercised for real
outside the test suite (tests never call external APIs: VERIFICATION_AI_ENABLED is
off while testing, so the rules-only path runs here)."""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import DoctorProfile

from .models import DoctorVerification
from .rules import run_rules
from .services import start_verification

User = get_user_model()
PASSWORD = "Sante-Solide-2026"
TEMP_MEDIA = tempfile.mkdtemp()
BIG_PDF = b"%PDF-1.4\n" + b"0" * 20_000 + b"\n%%EOF\n"


def make_doctor(email="doc@test.com", number="ORD-12345", first="Karim", last="Haddad", files=None, phone="0611111111"):
    user = User.objects.create_user(email, email, PASSWORD, first_name=first, last_name=last)
    doctor = DoctorProfile(user=user, phone=phone, specialty="general", license_number=number, city="Tunis")
    files = files or {}
    for i, field in enumerate(("diploma", "license_document")):
        content = files.get(field, BIG_PDF + f"{email}-{i}".encode())
        getattr(doctor, field).save(f"{field}.pdf", ContentFile(content), save=False)
    doctor.save()
    return doctor


def status_of(checks, key):
    return next(c["status"] for c in checks if c["key"] == key)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class RuleTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_clean_application_passes(self):
        checks = run_rules(make_doctor())
        self.assertTrue(all(c["status"] in ("ok", "info") for c in checks), checks)

    def test_fake_or_tiny_files_detected(self):
        doctor = make_doctor(files={"diploma": b"not a pdf at all", "license_document": b"%PDF tiny"})
        checks = run_rules(doctor)
        self.assertEqual(status_of(checks, "files_valid"), "fail")

    def test_same_file_for_two_documents(self):
        doctor = make_doctor(files={"diploma": BIG_PDF, "license_document": BIG_PDF})
        self.assertEqual(status_of(run_rules(doctor), "files_distinct"), "fail")

    def test_file_reused_from_another_account(self):
        make_doctor("a@test.com", "ORD-111", files={"diploma": BIG_PDF})
        doctor = make_doctor("b@test.com", "ORD-222", first="Yanis", files={"diploma": BIG_PDF})
        check = next(c for c in run_rules(doctor) if c["key"] == "files_reused")
        self.assertEqual(check["status"], "fail")
        self.assertIn("Karim Haddad", check["detail"])

    def test_account_signals(self):
        make_doctor("a@test.com", "ORD-111", phone="06 11 11 11 11")
        doctor = make_doctor("b@yopmail.com", "XX", phone="0611111111")  # same name, same phone
        checks = run_rules(doctor)
        for key in ("license_format", "email_domain", "phone_shared", "name_duplicate"):
            self.assertEqual(status_of(checks, key), "warning", key)


@override_settings(MEDIA_ROOT=TEMP_MEDIA, VERIFICATION_AI_ENABLED=False)
class ServiceAndBackofficeTests(TestCase):
    def test_without_ai_risk_is_never_low(self):
        verification = start_verification(make_doctor())
        self.assertEqual(verification.status, "done")
        self.assertEqual(verification.risk, "medium")
        self.assertEqual(verification.recommendation, "review")
        self.assertFalse(verification.ai_used)
        self.assertIn("pas été analysé par l'IA", verification.summary)

    def test_failed_check_gives_high_risk(self):
        verification = start_verification(make_doctor(files={"diploma": b"junk"}))
        self.assertEqual(verification.risk, "high")
        self.assertEqual(verification.recommendation, "reject")

    def test_signup_runs_the_analysis(self):
        data = {
            "first_name": "Nadia", "last_name": "Saidi", "email": "nadia@test.com", "phone": "0622222222",
            "password1": PASSWORD, "password2": PASSWORD, "accept_terms": "on", "certify": "on",
            "specialty": "general", "license_number": "ORD-777", "years_experience": "5", "city": "Sousse",
        }
        for field in ("diploma", "license_document"):
            data[field] = ContentFile(BIG_PDF + field.encode(), name=f"{field}.pdf")
        self.client.post(reverse("signup_doctor"), data)
        doctor = DoctorProfile.objects.get(user__email="nadia@test.com")
        self.assertEqual(doctor.verifications.get().status, "done")

    def test_staff_can_rerun_and_see_result(self):
        doctor = make_doctor()
        User.objects.create_user("admin@test.com", "admin@test.com", PASSWORD, is_staff=True)
        self.client.login(username="admin@test.com", password=PASSWORD)
        url = reverse("backoffice:doctor_detail", args=[doctor.pk])
        self.assertContains(self.client.get(url), "Lancer l'analyse")
        self.client.post(reverse("backoffice:doctor_verify", args=[doctor.pk]))
        self.assertEqual(DoctorVerification.objects.count(), 1)
        response = self.client.get(url)
        self.assertContains(response, "Risque moyen")
        self.assertContains(response, "Contrôles automatiques")
        self.assertContains(self.client.get(reverse("backoffice:doctor_list")), "Risque moyen")

    def test_doctor_cannot_trigger_analysis(self):
        doctor = make_doctor()
        self.client.login(username="doc@test.com", password=PASSWORD)
        self.client.post(reverse("backoffice:doctor_verify", args=[doctor.pk]))
        self.assertFalse(DoctorVerification.objects.exists())


class AiFloorTests(TestCase):
    def test_agent_findings_set_a_minimum_risk(self):
        from .services import ai_floor

        ok = {"key": "name_match", "status": "ok"}
        self.assertEqual(ai_floor([ok]), "low")
        self.assertEqual(ai_floor([ok, {"key": "diploma_model_match", "status": "warning"}]), "medium")
        self.assertEqual(ai_floor([ok, {"key": "license_model_match", "status": "fail"}]), "high")
