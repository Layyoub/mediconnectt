from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from core.models import DoctorProfile, PatientProfile
from records.models import (
    AccessLog,
    Contraindication,
    DoseLimit,
    DrugInteraction,
    MedicalRecord,
    Prescription,
    PrescriptionCheck,
    PatientInstructions,
)

from . import ai_agent, engine
from .matching import normalize_text, resolve_drug

User = get_user_model()


class PharmacheckTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(
            "doctor@pharmacheck.test", "doctor@pharmacheck.test", "Test-pass-2026!"
        )
        DoctorProfile.objects.create(
            user=self.doctor,
            phone="0600000000",
            specialty=DoctorProfile.Specialty.GENERAL,
            license_number="ORD-PC-1",
            city="Tunis",
            status=DoctorProfile.Status.VERIFIED,
        )
        self.patient = self._patient("patient@pharmacheck.test", date(1990, 1, 1))
        self.record = MedicalRecord.for_patient(self.patient)
        start = timezone.now() + timedelta(days=2)
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            start=start,
            end=start + timedelta(minutes=30),
            reason="Consultation de contrôle",
            status=Appointment.Status.CONFIRMED,
        )

    def _patient(self, email, birth_date):
        user = User.objects.create_user(email, email, "Test-pass-2026!")
        PatientProfile.objects.create(
            user=user, phone="0600000000", date_of_birth=birth_date
        )
        return user

    def _item(self, name, dosage="1 comprimé", frequency="1 fois par jour"):
        return {"drug_name": name, "dosage": dosage, "frequency": frequency, "duration": "5 jours"}

    def test_matching_normalizes_accents_and_strengths(self):
        self.assertEqual(normalize_text("Paracétamol 1 g, comprimé"), "paracetamol")
        match = resolve_drug("Paracétamol 1 g")
        self.assertEqual(match["dci"], "paracetamol")

    def test_brand_aliases_resolve_to_dci_and_class(self):
        self.assertEqual(resolve_drug("Doliprane 1 g")["dci"], "paracetamol")
        self.assertEqual(resolve_drug("Brufen")["drug_class"], "ains")

    def test_unknown_drug_is_an_explicit_info_finding(self):
        findings = engine.check(self.record, [self._item("Médicament inconnu")])
        self.assertTrue(any(f["kind"] == "unresolved" and f["severity"] == "info" for f in findings))

    def test_allergy_finding_uses_existing_allergy_conflicts_logic(self):
        self.record.allergies = "Allergie aux pénicillines"
        self.record.save()
        findings = engine.check(self.record, [self._item("amoxicilline")])
        self.assertTrue(any(f["kind"] == "allergy" and f["severity"] == "contraindicated" for f in findings))

    def test_penicillin_cephalosporin_cross_allergy_is_moderate(self):
        self.record.allergies = "Allergie aux céphalosporines"
        self.record.save()
        findings = engine.check(self.record, [self._item("amoxicilline")])
        self.assertTrue(any(
            f["kind"] == "allergy" and f["severity"] == "moderate" for f in findings
        ))

    def test_interaction_is_symmetric_against_current_treatments(self):
        self.record.current_treatments = "Warfarine"
        self.record.save()
        DrugInteraction.objects.create(
            group_a="warfarine", group_b="AINS",
            severity=DrugInteraction.Severity.MAJOR, advice="Association à réévaluer.",
        )
        findings = engine.check(self.record, [self._item("ibuprofène")])
        self.assertTrue(any(f["kind"] == "interaction" and f["severity"] == "major" for f in findings))

    def test_duplicate_active_ingredient_is_flagged(self):
        findings = engine.check(
            self.record, [self._item("Doliprane"), self._item("Paracétamol")]
        )
        self.assertTrue(any(f["kind"] == "duplicate_dci" for f in findings))

    def test_child_daily_dose_uses_weight_based_limit(self):
        child = self._patient("child@pharmacheck.test", date.today().replace(year=date.today().year - 8))
        record = MedicalRecord.objects.create(patient=child, weight_kg=25)
        DoseLimit.objects.create(
            dci="paracetamol", per_kg_daily_mg=60, max_daily_mg=3000,
            max_age_months=191, note="Limite de démonstration.",
        )
        findings = engine.check(
            record, [self._item("Paracétamol", "1000 mg", "2 fois par jour")]
        )
        self.assertTrue(any(f["kind"] == "dose" and f["severity"] == "major" for f in findings))

    def test_unparseable_dose_is_reported_as_information(self):
        DoseLimit.objects.create(dci="paracetamol", max_daily_mg=3000, note="Démonstration.")
        findings = engine.check(self.record, [self._item("Paracétamol", "selon besoin")])
        self.assertTrue(
            any(f["kind"] == "dose_unparsed" and f["severity"] == "info" for f in findings),
            repr(findings),
        )

    def test_age_and_condition_contraindications_are_reported(self):
        child = self._patient("young@pharmacheck.test", date.today().replace(year=date.today().year - 8))
        record = MedicalRecord.objects.create(patient=child, chronic_conditions="Asthme")
        Contraindication.objects.create(
            group="aspirine", max_age_months=191, severity="contraindicated",
            message="Démonstration : vérifier l'âge.",
        )
        findings = engine.check(record, [self._item("Aspirine")])
        self.assertTrue(
            any(f["kind"] == "contraindication" for f in findings), repr(findings)
        )

    def test_prescription_save_requires_and_records_severe_override(self):
        self.record.allergies = "Pénicilline"
        self.record.save()
        self.client.force_login(self.doctor)
        url = reverse("records:new_prescription", args=[self.patient.pk])
        data = {
            "appointment": self.appointment.pk,
            "valid_until": (timezone.localdate() + timedelta(days=20)).isoformat(),
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "10",
            "items-0-drug_name": "amoxicilline",
            "items-0-dosage": "500 mg",
            "items-0-frequency": "1 fois par jour",
            "items-0-duration": "5 jours",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Prescription.objects.exists())
        self.assertTrue(PrescriptionCheck.objects.filter(max_severity="contraindicated").exists())

        data["override_reason"] = "Bénéfice clinique évalué par le médecin."
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        prescription = Prescription.objects.get()
        self.assertTrue(prescription.safety_checks.filter(override_reason=data["override_reason"]).exists())

    def test_verified_doctor_access_is_required_for_check_endpoint(self):
        url = reverse("pharmacheck:verify")
        payload = {"record_id": self.record.pk, "items": [self._item("ibuprofène")]}
        self.client.force_login(self.doctor)
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AccessLog.objects.filter(action=AccessLog.Action.PRESCRIPTION_CHECK).exists())

        self.client.force_login(self.patient)
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_findings_and_badge_are_hidden_from_patient(self):
        prescription = Prescription.objects.create(record=self.record, doctor=self.doctor)
        PrescriptionCheck.objects.create(
            prescription=prescription,
            record=self.record,
            doctor=self.doctor,
            max_severity="major",
            findings=[{
                "kind": "interaction",
                "severity": "major",
                "severity_label": "Majeure",
                "message": "Alerte confidentielle réservée au médecin.",
            }],
        )
        url = reverse("records:prescription", args=[prescription.code])
        self.client.force_login(self.patient)
        patient_response = self.client.get(url)
        self.assertNotContains(patient_response, "Alerte confidentielle")
        self.assertNotContains(patient_response, "Pharma-Check")

        self.client.force_login(self.doctor)
        doctor_response = self.client.get(url)
        self.assertContains(doctor_response, "Alerte confidentielle réservée au médecin.")
        self.assertContains(doctor_response, "Pharma-Check")

        other = User.objects.create_user("other@pharmacheck.test", "other@pharmacheck.test", "Test-pass-2026!")
        DoctorProfile.objects.create(
            user=other,
            phone="0600000000",
            specialty=DoctorProfile.Specialty.GENERAL,
            license_number="ORD-PC-2",
            city="Tunis",
            status=DoctorProfile.Status.VERIFIED,
        )
        self.client.force_login(other)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_mocked_ai_opinion_can_only_raise_rules_level(self):
        check = PrescriptionCheck.objects.create(
            record=self.record,
            doctor=self.doctor,
            max_severity="major",
            findings=[{"severity": "major"}],
        )
        opinion = ai_agent.PharmacheckOpinion(
            overall_level="info",
            resolved_drugs=[],
            extra_findings=[],
            alternatives=[],
            pharmacist_comment="Seconde lecture prudente.",
        )
        with patch("pharmacheck.ai_agent.run_agent", return_value=opinion):
            ai_agent.analyse(check)
        check.refresh_from_db()
        self.assertEqual(check.ai_status, PrescriptionCheck.AIStatus.DONE)
        self.assertEqual(check.ai_opinion["final_level"], "major")
        raised_opinion = ai_agent.PharmacheckOpinion(
            overall_level="contraindicated",
            resolved_drugs=[],
            extra_findings=[],
            alternatives=[],
            pharmacist_comment="Réévaluation médicale recommandée.",
        )
        with patch("pharmacheck.ai_agent.run_agent", return_value=raised_opinion):
            ai_agent.analyse(check)
        check.refresh_from_db()
        self.assertEqual(check.ai_opinion["final_level"], "contraindicated")

    def test_ai_failure_keeps_rule_findings(self):
        check = PrescriptionCheck.objects.create(
            record=self.record,
            doctor=self.doctor,
            max_severity="major",
            findings=[{"kind": "interaction", "severity": "major"}],
        )
        with patch("pharmacheck.ai_agent.run_agent", side_effect=RuntimeError("provider unavailable")):
            ai_agent.analyse(check)
        check.refresh_from_db()
        self.assertEqual(check.ai_status, PrescriptionCheck.AIStatus.ERROR)
        self.assertEqual(check.findings[0]["severity"], "major")
        self.assertEqual(check.max_severity, "major")

    def test_resolver_confirmation_is_used_by_the_rules_engine(self):
        self.record.current_treatments = "Warfarine"
        self.record.save()
        interaction = DrugInteraction.objects.create(
            group_a="warfarine", group_b="AINS",
            severity=DrugInteraction.Severity.MAJOR, advice="Association à réévaluer.",
        )
        source = PrescriptionCheck.objects.create(
            record=self.record,
            doctor=self.doctor,
            checked_items=[self._item("voltaren")],
            findings=[{"kind": "unresolved", "severity": "info", "medications": ["voltaren"]}],
            ai_status=PrescriptionCheck.AIStatus.DONE,
            ai_opinion={
                "resolved_drugs": [{
                    "typed": "voltaren",
                    "dci": "diclofenac",
                    "drug_class": "ains",
                    "confidence": 0.92,
                    "note": "Nom proche d'une marque connue.",
                    "based_on": ["traitement en cours"],
                }],
                "final_level": "info",
            },
        )
        payload = {
            "record_id": self.record.pk,
            "items": [self._item("voltaren")],
            "confirmed_resolutions": [{
                "typed": "voltaren",
                "dci": "diclofenac",
                "drug_class": "ains",
                "source_check_id": source.pk,
            }],
        }
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("pharmacheck:verify"), payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(finding["kind"] == "interaction" for finding in response.json()["findings"]),
            f"Expected interaction rule {interaction.pk} to match the confirmed DCI.",
        )
        result = PrescriptionCheck.objects.exclude(pk=source.pk).latest("created_at")
        self.assertEqual(result.confirmed_resolutions[0]["dci"], "diclofenac")

    def test_ai_only_finding_does_not_block_prescription_save(self):
        PrescriptionCheck.objects.create(
            record=self.record,
            doctor=self.doctor,
            max_severity="major",
            findings=[],
            ai_opinion={
                "final_level": "major",
                "extra_findings": [{
                    "kind": "other", "severity": "major", "title": "IA",
                    "detail": "Piste uniquement IA.",
                }],
            },
            ai_status=PrescriptionCheck.AIStatus.DONE,
        )
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("records:new_prescription", args=[self.patient.pk]),
            {
                "appointment": self.appointment.pk,
                "valid_until": (timezone.localdate() + timedelta(days=20)).isoformat(),
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "10",
                "items-0-drug_name": "paracetamol",
                "items-0-dosage": "500 mg", "items-0-frequency": "1 fois par jour",
                "items-0-duration": "5 jours",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Prescription.objects.exists())

    def test_non_following_doctor_gets_not_found_for_prescription_form(self):
        other = User.objects.create_user("other-doctor@pharmacheck.test", "other-doctor@pharmacheck.test", "Test-pass-2026!")
        DoctorProfile.objects.create(
            user=other,
            phone="0600000000",
            specialty=DoctorProfile.Specialty.GENERAL,
            license_number="ORD-PC-3",
            city="Tunis",
            status=DoctorProfile.Status.VERIFIED,
        )
        self.client.force_login(other)
        response = self.client.get(reverse("records:new_prescription", args=[self.patient.pk]))
        self.assertEqual(response.status_code, 404)

    def test_patient_instructions_are_hidden_until_doctor_approval(self):
        prescription = Prescription.objects.create(record=self.record, doctor=self.doctor)
        instructions = PatientInstructions.objects.create(
            prescription=prescription,
            text_fr="Prenez le médicament comme indiqué.",
            text_ar="تناول الدواء حسب التعليمات.",
            generation_status=PatientInstructions.GenerationStatus.DONE,
        )
        url = reverse("records:prescription", args=[prescription.code])
        self.client.force_login(self.patient)
        response = self.client.get(url)
        self.assertNotContains(response, "Prenez le médicament comme indiqué.")
        self.assertNotContains(response, "تناول الدواء حسب التعليمات.")

        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("records:patient_instructions", args=[prescription.code]),
            {
                "text_fr": instructions.text_fr,
                "text_ar": instructions.text_ar,
                "instruction_action": "approve",
            },
        )
        self.assertEqual(response.status_code, 302)
        instructions.refresh_from_db()
        self.assertEqual(instructions.status, PatientInstructions.Status.APPROVED)

        self.client.force_login(self.patient)
        response = self.client.get(url)
        self.assertContains(response, "Prenez le médicament comme indiqué.")
        self.assertContains(response, "تناول الدواء حسب التعليمات.")
        self.assertContains(response, "Rédigé avec l'aide d'une IA et validé par votre médecin")


class RuleSeverityTests(TestCase):
    def test_rule_results_are_ranked_by_severity(self):
        findings = [
            {"severity": "info"},
            {"severity": "moderate"},
            {"severity": "major"},
        ]
        self.assertEqual(engine.max_severity(findings), "major")
