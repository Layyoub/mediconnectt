from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import PatientProfile

from .engine import complete_session, evaluate
from .models import RedFlagRule, SymptomEntry, TriageLevel, TriageSession

User = get_user_model()
PASSWORD = "Sante-Solide-2026"


def make_patient(email="p@test.com", birth=date(1990, 5, 1)):
    user = User.objects.create_user(email, email, PASSWORD, first_name="Amina", last_name="Ben")
    PatientProfile.objects.create(user=user, phone="0600", date_of_birth=birth)
    return user


def make_session(user, symptoms, temperature=None, risk=(), age=35):
    session = TriageSession.objects.create(
        patient=user, chief_complaint="Je ne me sens pas bien", temperature=temperature,
        risk_factors=list(risk), age=age,
    )
    for code, severity, duration in symptoms:
        SymptomEntry.objects.create(session=session, symptom=code, severity=severity, duration=duration)
    return session


class EngineTests(TestCase):
    """The red-flag rules come from the data migration, so they exist in tests."""

    def setUp(self):
        self.user = make_patient()

    def test_seeded_rules_exist(self):
        self.assertGreaterEqual(RedFlagRule.objects.filter(is_active=True).count(), 10)

    def test_minor_symptom_is_low(self):
        outcome = evaluate(make_session(self.user, [("runny_nose", 1, "lt24h")]))
        self.assertEqual(outcome.level, TriageLevel.LOW)
        self.assertEqual(outcome.suggested_specialty, "ent")

    def test_flu_like_symptoms_are_moderate(self):
        session = make_session(self.user, [
            ("fever", 2, "1-3d"), ("cough", 2, "1-3d"), ("fatigue", 2, "1-3d"),
        ])
        self.assertEqual(evaluate(session).level, TriageLevel.MODERATE)

    def test_risk_factors_and_age_raise_score(self):
        symptoms = [("fever", 2, "1-3d"), ("cough", 2, "1-3d")]
        young = evaluate(make_session(self.user, symptoms, age=30))
        fragile = evaluate(make_session(self.user, symptoms, age=70, risk=["chronic", "immuno"]))
        self.assertGreater(fragile.score, young.score)
        self.assertGreater(
            list(TriageLevel.values).index(fragile.level), list(TriageLevel.values).index(young.level)
        )

    def test_severe_chest_pain_is_urgent_through_red_flag(self):
        outcome = evaluate(make_session(self.user, [("chest_pain", 3, "lt24h")]))
        self.assertEqual(outcome.level, TriageLevel.URGENT)
        self.assertIn("Douleur thoracique sévère", [r.name for r in outcome.red_flags])
        self.assertEqual(outcome.suggested_specialty, "cardiology")

    def test_any_chest_pain_is_at_least_high(self):
        outcome = evaluate(make_session(self.user, [("chest_pain", 1, "lt24h")]))
        self.assertEqual(outcome.level, TriageLevel.HIGH)

    def test_flu_with_fever_stays_moderate(self):
        session = make_session(
            self.user, [("fever", 2, "1-3d"), ("cough", 2, "1-3d"), ("fatigue", 2, "1-3d")],
            temperature=Decimal("38.5"),
        )
        self.assertEqual(evaluate(session).level, TriageLevel.MODERATE)

    def test_combined_rule_needs_both_symptoms(self):
        alone = evaluate(make_session(self.user, [("stiff_neck", 1, "lt24h")]))
        self.assertNotIn("Maux de tête avec raideur de nuque et fièvre", [r.name for r in alone.red_flags])
        both = evaluate(make_session(self.user, [("stiff_neck", 1, "lt24h"), ("fever", 1, "lt24h")]))
        self.assertEqual(both.level, TriageLevel.URGENT)

    def test_temperature_rule(self):
        outcome = evaluate(make_session(self.user, [("fatigue", 1, "lt24h")], temperature=Decimal("40.2")))
        self.assertEqual(outcome.level, TriageLevel.HIGH)

    def test_red_flags_never_lower_the_level(self):
        RedFlagRule.objects.create(name="Test", symptom="runny_nose", level=TriageLevel.HIGH, message="x")
        session = make_session(self.user, [("chest_pain", 3, "lt24h"), ("runny_nose", 1, "lt24h")])
        self.assertEqual(evaluate(session).level, TriageLevel.URGENT)

    def test_inactive_rule_is_ignored(self):
        RedFlagRule.objects.filter(name="Signes d'AVC").update(is_active=False)
        outcome = evaluate(make_session(self.user, [("stroke_signs", 1, "lt24h")]))
        self.assertNotIn("Signes d'AVC", [r.name for r in outcome.red_flags])

    def test_child_is_oriented_to_pediatrics(self):
        outcome = evaluate(make_session(self.user, [("fever", 2, "1-3d")], age=6))
        self.assertEqual(outcome.suggested_specialty, "pediatrics")

    def test_complete_session_stores_result(self):
        session = make_session(self.user, [("chest_pain", 3, "lt24h")])
        result = complete_session(session)
        session.refresh_from_db()
        self.assertTrue(session.is_completed)
        self.assertEqual(result.level, TriageLevel.URGENT)
        self.assertSetEqual(
            set(result.red_flags.values_list("name", flat=True)),
            {"Douleur thoracique sévère", "Douleur thoracique"},
        )


class FlowTests(TestCase):
    def setUp(self):
        self.user = make_patient()
        self.client.login(username="p@test.com", password=PASSWORD)

    def test_full_patient_flow(self):
        # Step 1: context
        response = self.client.post(reverse("triage:start"), {
            "chief_complaint": "Fièvre et toux depuis trois jours",
            "temperature": "38.6",
            "risk_factors": ["smoker"],
        })
        session = TriageSession.objects.get(patient=self.user)
        self.assertRedirects(response, reverse("triage:symptoms", args=[session.pk]))
        self.assertEqual(session.age, TriageSession.age_from_birth_date(date(1990, 5, 1)))

        # Step 2: symptoms (empty selection is refused)
        url = reverse("triage:symptoms", args=[session.pk])
        self.assertContains(self.client.post(url, {}), "Sélectionnez au moins un symptôme")
        self.assertContains(self.client.post(url, {"s_fever": "on", "sev_fever": "2"}), "Indiquez depuis quand")
        response = self.client.post(url, {
            "s_fever": "on", "sev_fever": "2", "dur_fever": "1-3d",
            "s_cough": "on", "sev_cough": "2", "dur_cough": "1-3d",
            "sev_rash": "1",  # severity of an unchecked symptom is ignored
        })
        self.assertRedirects(response, reverse("triage:review", args=[session.pk]))
        self.assertEqual(session.symptoms.count(), 2)

        # Step 3: review then analyse
        self.assertContains(self.client.get(reverse("triage:review", args=[session.pk])), "Toux")
        response = self.client.post(reverse("triage:review", args=[session.pk]))
        self.assertRedirects(response, reverse("triage:result", args=[session.pk]))

        # Step 4: result
        response = self.client.get(reverse("triage:result", args=[session.pk]))
        self.assertContains(response, "Niveau d'urgence")
        self.assertContains(response, "Modéré")

        # A completed session can no longer be edited
        self.assertRedirects(self.client.get(url), reverse("triage:result", args=[session.pk]))

        # It shows on the dashboard and in the history
        self.assertContains(self.client.get(reverse("patient_dashboard")), "Voir le résultat détaillé")
        self.assertContains(self.client.get(reverse("triage:home")), "Fièvre et toux")

    def test_editing_symptoms_replaces_previous_selection(self):
        session = make_session(self.user, [("fever", 2, "1-3d")])
        self.client.post(reverse("triage:symptoms", args=[session.pk]), {
            "s_rash": "on", "sev_rash": "1", "dur_rash": "lt24h",
        })
        self.assertEqual(list(session.symptoms.values_list("symptom", flat=True)), ["rash"])

    def test_patient_cannot_see_another_patients_session(self):
        other = make_patient("other@test.com")
        session = make_session(other, [("fever", 1, "lt24h")])
        complete_session(session)
        for name in ["triage:result", "triage:symptoms", "triage:review", "triage:context"]:
            self.assertEqual(self.client.get(reverse(name, args=[session.pk])).status_code, 404)

    def test_cancel_in_progress_session(self):
        session = make_session(self.user, [])
        self.client.post(reverse("triage:cancel", args=[session.pk]))
        self.assertFalse(TriageSession.objects.filter(pk=session.pk).exists())

    def test_doctor_cannot_use_patient_triage(self):
        User.objects.create_user("admin@test.com", "admin@test.com", PASSWORD, is_staff=True)
        self.client.login(username="admin@test.com", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("triage:start")).status_code, 302)

    def test_backoffice_triage_page_and_toggle(self):
        complete_session(make_session(self.user, [("chest_pain", 3, "lt24h")]))
        User.objects.create_user("admin@test.com", "admin@test.com", PASSWORD, is_staff=True)
        self.client.login(username="admin@test.com", password=PASSWORD)
        response = self.client.get(reverse("backoffice:triage"))
        self.assertContains(response, "Douleur thoracique sévère")
        rule = RedFlagRule.objects.get(name="Douleur thoracique sévère")
        self.client.post(reverse("backoffice:toggle_rule", args=[rule.pk]))
        rule.refresh_from_db()
        self.assertFalse(rule.is_active)


class AIAgentTests(TestCase):
    """The real OpenAI API is never called here: run_agent is replaced by a fake."""

    def setUp(self):
        self.user = make_patient()

    def fake_output(self, **overrides):
        from .ai_agent import AITriageOutput

        data = {
            "level": "moderate",
            "summary": "Analyse personnalisée de test.",
            "recommendation": "Consultez un médecin sous 48 h.",
            "reasons": ["Fièvre modérée", "Toux"],
            "warning_signs": ["Difficulté à respirer"],
            "suggested_specialty": "general",
        }
        data.update(overrides)
        return AITriageOutput(**data)

    def test_ai_result_is_saved(self):
        from unittest import mock

        session = make_session(self.user, [("fever", 2, "1-3d"), ("cough", 2, "1-3d")])
        with self.settings(TRIAGE_AI_ENABLED=True, OPENAI_MODEL="gpt-test"), \
                mock.patch("triage.ai_agent.run_agent", return_value=self.fake_output()):
            result = complete_session(session)
        self.assertTrue(result.by_ai)
        self.assertEqual(result.engine_version, "langchain:gpt-test")
        self.assertEqual(result.summary, "Analyse personnalisée de test.")
        self.assertEqual(result.recommendation, "Consultez un médecin sous 48 h.")
        self.assertEqual(result.warning_signs, ["Difficulté à respirer"])
        self.assertEqual(result.reasons[0], {"label": "Fièvre modérée", "points": None})

    def test_ai_cannot_go_below_red_flag(self):
        from unittest import mock

        session = make_session(self.user, [("chest_pain", 3, "lt24h")])
        with self.settings(TRIAGE_AI_ENABLED=True), \
                mock.patch("triage.ai_agent.run_agent", return_value=self.fake_output(level="low")):
            result = complete_session(session)
        self.assertEqual(result.level, TriageLevel.URGENT)
        self.assertIn("services d'urgence", result.recommendation)  # safety wording, not the AI's

    def test_ai_failure_falls_back_to_rules(self):
        from unittest import mock

        session = make_session(self.user, [("fever", 2, "1-3d")])
        with self.settings(TRIAGE_AI_ENABLED=True), \
                mock.patch("triage.ai_agent.run_agent", side_effect=TimeoutError("API down")), \
                self.assertLogs("triage", level="ERROR"):
            result = complete_session(session)
        self.assertFalse(result.by_ai)
        self.assertEqual(result.engine_version, "rules-v1")
        self.assertTrue(TriageSession.objects.get(pk=session.pk).is_completed)

    def test_ai_disabled_uses_rules_only(self):
        from unittest import mock

        session = make_session(self.user, [("fever", 2, "1-3d")])
        with self.settings(TRIAGE_AI_ENABLED=False), mock.patch("triage.ai_agent.run_agent") as agent:
            complete_session(session)
        agent.assert_not_called()

    def test_patient_identity_is_not_sent(self):
        from .ai_agent import describe_session

        session = make_session(self.user, [("fever", 2, "1-3d")], temperature=Decimal("38.5"))
        text = describe_session(session)
        self.assertIn("Fièvre", text)
        self.assertIn("38.5", text)
        for private in ["Amina", "Ben", "p@test.com"]:
            self.assertNotIn(private, text)

    def test_tool_reports_red_flag_floor(self):
        import json

        from .ai_agent import build_tools

        session = make_session(self.user, [("chest_pain", 1, "lt24h")])
        rules_tool, specialties_tool = build_tools(evaluate(session))
        data = json.loads(rules_tool.invoke({}))
        self.assertEqual(data["niveau_minimum"], "high")
        self.assertIn("cardiology", json.loads(specialties_tool.invoke({})))

    def test_result_page_shows_ai_analysis(self):
        from unittest import mock

        session = make_session(self.user, [("fever", 2, "1-3d")])
        with self.settings(TRIAGE_AI_ENABLED=True), \
                mock.patch("triage.ai_agent.run_agent", return_value=self.fake_output()):
            complete_session(session)
        self.client.login(username="p@test.com", password=PASSWORD)
        response = self.client.get(reverse("triage:result", args=[session.pk]))
        self.assertContains(response, "Analyse de l'assistant IA")
        self.assertContains(response, "Difficulté à respirer")
