import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import DoctorProfile, PatientProfile

User = get_user_model()
TEMP_MEDIA = tempfile.mkdtemp()
PASSWORD = "Sante-Solide-2026"


def pdf(name="doc.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def make_patient(email="patient@test.com"):
    user = User.objects.create_user(email, email, PASSWORD, first_name="Amina", last_name="Ben")
    PatientProfile.objects.create(user=user, phone="0600000000")
    return user


def make_doctor(email="doc@test.com", status=DoctorProfile.Status.PENDING, number="ORD-1"):
    user = User.objects.create_user(email, email, PASSWORD, first_name="Karim", last_name="Haddad")
    DoctorProfile.objects.create(
        user=user, phone="0600000000", specialty="general", license_number=number, city="Ville",
        diploma=pdf(), license_document=pdf(), status=status,
    )
    return user


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class MediConnectTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_public_pages_render(self):
        for name in ["home", "login", "signup", "signup_patient", "signup_doctor", "password_reset", "legal", "coming_soon"]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_patient_signup_logs_in_and_shows_dashboard(self):
        response = self.client.post(reverse("signup_patient"), {
            "first_name": "Amina", "last_name": "Ben", "email": "Amina@Test.com", "phone": "0600000000",
            "password1": PASSWORD, "password2": PASSWORD, "accept_terms": "on",
        }, follow=True)
        self.assertRedirects(response, reverse("patient_dashboard"))
        self.assertContains(response, "Bonjour, Amina")
        self.assertTrue(PatientProfile.objects.filter(user__email="amina@test.com").exists())

    def test_patient_signup_rejects_duplicate_email_and_mismatch(self):
        make_patient()
        response = self.client.post(reverse("signup_patient"), {
            "first_name": "A", "last_name": "B", "email": "patient@test.com", "phone": "1",
            "password1": PASSWORD, "password2": "autre", "accept_terms": "on",
        })
        self.assertContains(response, "Un compte existe déjà")
        self.assertContains(response, "ne correspondent pas")

    def test_doctor_signup_creates_pending_profile(self):
        response = self.client.post(reverse("signup_doctor"), {
            "first_name": "Karim", "last_name": "Haddad", "email": "k@test.com", "phone": "0600",
            "specialty": "cardiology", "license_number": "ord-42", "years_experience": 5, "city": "Ville",
            "diploma": pdf(), "license_document": pdf("ordre.png"),
            "password1": PASSWORD, "password2": PASSWORD, "accept_terms": "on", "certify": "on",
        }, follow=True)
        self.assertRedirects(response, reverse("doctor_dashboard"))
        self.assertContains(response, "Vérification en cours")
        profile = DoctorProfile.objects.get(user__email="k@test.com")
        self.assertEqual(profile.license_number, "ORD-42")
        self.assertEqual(profile.status, DoctorProfile.Status.PENDING)

    def test_doctor_signup_rejects_bad_file_type(self):
        response = self.client.post(reverse("signup_doctor"), {
            "first_name": "K", "last_name": "H", "email": "k2@test.com", "phone": "0600",
            "specialty": "general", "license_number": "X1", "years_experience": 1, "city": "V",
            "diploma": SimpleUploadedFile("virus.exe", b"MZ"), "license_document": pdf(),
            "password1": PASSWORD, "password2": PASSWORD, "accept_terms": "on", "certify": "on",
        })
        self.assertContains(response, "Formats acceptés")
        self.assertFalse(User.objects.filter(email="k2@test.com").exists())

    def test_login_by_email_redirects_by_role(self):
        make_patient()
        response = self.client.post(reverse("login"), {"username": "PATIENT@test.com", "password": PASSWORD}, follow=True)
        self.assertRedirects(response, reverse("patient_dashboard"))

    def test_role_isolation(self):
        make_patient()
        self.client.login(username="patient@test.com", password=PASSWORD)
        self.assertRedirects(self.client.get(reverse("doctor_dashboard")), reverse("dashboard"), target_status_code=302)
        response = self.client.get(reverse("backoffice:overview"))
        self.assertEqual(response.status_code, 302)

    def test_verified_doctor_sees_full_dashboard(self):
        make_doctor(status=DoctorProfile.Status.VERIFIED)
        self.client.login(username="doc@test.com", password=PASSWORD)
        response = self.client.get(reverse("doctor_dashboard"))
        self.assertContains(response, "Demandes de consultation")
        self.assertContains(response, "Compte vérifié")
        self.assertNotContains(response, "Disponible après vérification")

    def test_backoffice_approve_and_reject_flow(self):
        User.objects.create_user("admin@test.com", "admin@test.com", PASSWORD, is_staff=True)
        doctor = make_doctor().doctor_profile
        self.client.login(username="admin@test.com", password=PASSWORD)

        for name in ["backoffice:overview", "backoffice:doctor_list", "backoffice:patient_list"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertContains(self.client.get(reverse("backoffice:doctor_detail", args=[doctor.pk])), "Justificatifs")

        url = reverse("backoffice:doctor_decision", args=[doctor.pk])
        self.assertEqual(self.client.post(url, {"decision": "reject"}).status_code, 400)  # reason required
        self.client.post(url, {"decision": "reject", "reason": "Diplôme illisible"})
        doctor.refresh_from_db()
        self.assertEqual(doctor.status, DoctorProfile.Status.REJECTED)

        self.client.post(url, {"decision": "approve"})
        doctor.refresh_from_db()
        self.assertEqual(doctor.status, DoctorProfile.Status.VERIFIED)
        self.assertEqual(doctor.rejection_reason, "")

    def test_documents_are_staff_only(self):
        doctor = make_doctor().doctor_profile
        url = reverse("backoffice:doctor_document", args=[doctor.pk, "diplome"])
        self.assertEqual(self.client.get(url).status_code, 302)  # anonymous -> login
        self.client.login(username="doc@test.com", password=PASSWORD)
        self.assertEqual(self.client.get(url).status_code, 302)  # the doctor themself -> no access
        User.objects.create_user("admin@test.com", "admin@test.com", PASSWORD, is_staff=True)
        self.client.login(username="admin@test.com", password=PASSWORD)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse("backoffice:doctor_document", args=[doctor.pk, "autre"])).status_code, 404)

    def test_password_reset_sends_email(self):
        make_patient()
        response = self.client.post(reverse("password_reset"), {"email": "patient@test.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/reinitialiser/", mail.outbox[0].body)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class AccountTests(TestCase):
    def test_patient_updates_profile(self):
        user = make_patient()
        self.client.login(username=user.username, password=PASSWORD)
        url = reverse("account_profile")
        self.assertContains(self.client.get(url), "Mon profil")
        response = self.client.post(url, {
            "first_name": "Amina", "last_name": "Belhadj", "phone": "0611111111",
            "date_of_birth": "1992-03-14", "gender": "F",
        })
        self.assertRedirects(response, url)
        user.refresh_from_db()
        self.assertEqual(user.last_name, "Belhadj")
        self.assertEqual(str(user.patient_profile.date_of_birth), "1992-03-14")

    def test_birth_date_in_future_rejected(self):
        user = make_patient()
        self.client.login(username=user.username, password=PASSWORD)
        response = self.client.post(reverse("account_profile"), {
            "first_name": "A", "last_name": "B", "phone": "06", "date_of_birth": "2999-01-01",
        })
        self.assertContains(response, "dans le futur")

    def test_doctor_cannot_change_verified_fields(self):
        user = make_doctor(status=DoctorProfile.Status.VERIFIED)
        self.client.login(username=user.username, password=PASSWORD)
        self.client.post(reverse("account_profile"), {
            "first_name": "Karim", "last_name": "Haddad", "phone": "0600000000", "years_experience": "12",
            "city": "Sfax", "bio": "Pneumologue.", "specialty": "cardiology", "license_number": "HACK",
        })
        profile = DoctorProfile.objects.get(user=user)
        self.assertEqual((profile.city, profile.years_experience), ("Sfax", 12))
        self.assertEqual((profile.specialty, profile.license_number), ("general", "ORD-1"))

    def test_change_email_requires_password_and_unique_address(self):
        user = make_patient()
        make_patient("pris@test.com")
        self.client.login(username=user.username, password=PASSWORD)
        url = reverse("account_settings")
        response = self.client.post(url, {"action": "email", "email": "Nouveau@Test.com", "password": "faux"})
        self.assertContains(response, "Mot de passe incorrect")
        response = self.client.post(url, {"action": "email", "email": "pris@test.com", "password": PASSWORD})
        self.assertContains(response, "existe déjà")

        self.client.post(url, {"action": "email", "email": "Nouveau@Test.com", "password": PASSWORD})
        user.refresh_from_db()
        self.assertEqual((user.email, user.username), ("nouveau@test.com", "nouveau@test.com"))
        self.assertEqual(mail.outbox[-1].to, ["patient@test.com"])  # old address warned
        self.client.logout()
        self.assertTrue(self.client.login(username="nouveau@test.com", password=PASSWORD))

    def test_change_password_keeps_session(self):
        user = make_doctor()
        self.client.login(username=user.username, password=PASSWORD)
        url = reverse("account_settings")
        response = self.client.post(url, {
            "action": "password", "old_password": PASSWORD,
            "new_password1": "Nouveau-Mot-2026", "new_password2": "Nouveau-Mot-2026",
        })
        self.assertRedirects(response, url)
        self.assertEqual(self.client.get(url).status_code, 200)  # still logged in
        user.refresh_from_db()
        self.assertTrue(user.check_password("Nouveau-Mot-2026"))

    def test_admin_profile_redirects_to_settings(self):
        User.objects.create_user("admin@test.com", "admin@test.com", PASSWORD, is_staff=True)
        self.client.login(username="admin@test.com", password=PASSWORD)
        self.assertRedirects(self.client.get(reverse("account_profile")), reverse("account_settings"))
        self.assertEqual(self.client.get(reverse("account_settings")).status_code, 200)
