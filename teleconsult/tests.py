import json
import tempfile
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from appointments.tests import PASSWORD, make_doctor, make_patient
from records.models import DocumentAnalysis, MedicalDocument

from .models import ChatMessage, ConsultationRoom, ConsultationSummary, Notification
from .services import room_for


class RoomTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.appointment = self._appointment(timedelta(days=2))
        self.room = room_for(self.appointment)

    def _appointment(self, delay, status=Appointment.Status.CONFIRMED, patient=None):
        start = timezone.now() + delay
        return Appointment.objects.create(
            patient=patient or self.patient, doctor=self.doctor, start=start, end=start + timedelta(minutes=30),
            reason="Toux", status=status,
        )

    def login(self, who):
        email = "doc@test.fr" if who == "doctor" else "patient@test.fr"
        self.client.login(username=email, password=PASSWORD)

    def url(self, name, room=None):
        return reverse(f"teleconsult:{name}", args=[(room or self.room).code])


class LifecycleTests(RoomTestCase):
    def test_accepting_an_appointment_creates_the_room(self):
        pending = self._appointment(timedelta(days=3), status=Appointment.Status.PENDING)
        self.login("doctor")
        self.client.post(reverse("appointments:decide", args=[pending.pk]), {"decision": "accept"})
        self.assertTrue(ConsultationRoom.objects.filter(appointment=pending).exists())
        notif = Notification.objects.get(user=self.patient, kind="confirmed")
        self.assertEqual(notif.url, reverse("appointments:detail", args=[pending.pk]))

    def test_patient_enters_waiting_room_any_time_without_starting(self):
        # Appointment is in 2 days: the patient can still enter the waiting room.
        self.login("patient")
        response = self.client.get(self.url("room"))
        self.assertContains(response, "salle d'attente")
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "scheduled")
        self.assertTrue(self.room.patient_waiting)
        # Chat is not available until the doctor arrives.
        self.assertEqual(self.client.post(self.url("chat"), {"body": "Bonjour"}).status_code, 409)
        self.assertEqual(self.client.get(self.url("chat")).json()["status"], "scheduled")

    def test_doctor_is_notified_once_that_patient_waits(self):
        self.login("patient")
        self.client.get(self.url("room"))
        self.client.get(self.url("room"))
        self.assertEqual(Notification.objects.filter(user=self.doctor, kind="waiting").count(), 1)
        self.login("doctor")
        self.assertContains(self.client.get(reverse("doctor_dashboard")), "vous attend")

    def test_consultation_starts_when_doctor_enters(self):
        self.login("doctor")
        response = self.client.get(self.url("room"))
        self.assertContains(response, "data-room")
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_open)
        self.assertIsNotNone(self.room.opened_at)
        self.assertTrue(Notification.objects.filter(user=self.patient, kind="room_open").exists())
        self.assertEqual(self.room.messages.get().kind, "system")
        # The waiting patient's next poll sees the consultation has started.
        self.login("patient")
        self.assertEqual(self.client.get(self.url("chat")).json()["status"], "open")
        self.assertEqual(self.client.post(self.url("chat"), {"body": "Bonjour"}).status_code, 200)

    def test_doctor_reentering_does_not_restart(self):
        self.login("doctor")
        self.client.get(self.url("room"))
        self.client.get(self.url("room"))
        self.assertEqual(self.room.messages.count(), 1)

    def test_outsiders_get_404(self):
        make_patient("other@test.fr")
        self.client.login(username="other@test.fr", password=PASSWORD)
        for name in ("room", "chat", "signal", "finish"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 404, name)

    def test_cancelled_appointment_room_is_unreachable(self):
        Appointment.objects.filter(pk=self.appointment.pk).update(status=Appointment.Status.CANCELLED)
        self.login("patient")
        self.assertEqual(self.client.get(self.url("room")).status_code, 404)


class ChatTests(RoomTestCase):
    def setUp(self):
        super().setUp()
        self.room.open()

    def test_messages_round_trip(self):
        self.login("patient")
        response = self.client.post(self.url("chat"), {"body": "  Bonjour docteur  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"]["body"], "Bonjour docteur")
        self.assertTrue(response.json()["message"]["mine"])

        self.login("doctor")
        data = self.client.get(self.url("chat"), {"after": 0}).json()
        self.assertEqual(len(data["messages"]), 1)
        self.assertFalse(data["messages"][0]["mine"])
        self.assertEqual(data["messages"][0]["sender"], "Amina Belhadj")
        last = data["messages"][0]["id"]
        self.assertEqual(self.client.get(self.url("chat"), {"after": last}).json()["messages"], [])

    def test_presence(self):
        self.login("doctor")
        self.client.get(self.url("chat"))
        self.login("patient")
        self.assertTrue(self.client.get(self.url("chat")).json()["other_online"])

    def test_empty_and_too_long_messages_rejected(self):
        self.login("patient")
        self.assertEqual(self.client.post(self.url("chat"), {"body": "   "}).status_code, 400)
        self.assertEqual(self.client.post(self.url("chat"), {"body": "x" * 2001}).status_code, 400)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_offline_recipient_gets_one_notification(self):
        self.login("patient")
        self.client.post(self.url("chat"), {"body": "Bonjour"})
        self.client.post(self.url("chat"), {"body": "Vous êtes là ?"})
        self.assertEqual(Notification.objects.filter(user=self.doctor, kind="message").count(), 1)

    def test_html_is_not_interpreted(self):
        self.login("patient")
        self.client.post(self.url("chat"), {"body": "<script>alert(1)</script>"})
        msg = ChatMessage.objects.get()
        self.assertEqual(msg.body, "<script>alert(1)</script>")  # stored raw, rendered with textContent / autoescape


class SignalTests(RoomTestCase):
    def setUp(self):
        super().setUp()
        self.room.open()

    def post_signal(self, payload):
        return self.client.post(self.url("signal"), json.dumps(payload), content_type="application/json")

    def test_signals_are_delivered_once_to_the_other_side(self):
        self.login("doctor")
        self.post_signal({"type": "offer", "data": {"type": "offer", "sdp": "v=0"}})
        self.assertEqual(self.client.get(self.url("signal")).json()["signals"], [])  # not for the sender
        self.login("patient")
        signals = self.client.get(self.url("signal")).json()["signals"]
        self.assertEqual(signals, [{"type": "offer", "data": {"type": "offer", "sdp": "v=0"}}])
        self.assertEqual(self.client.get(self.url("signal")).json()["signals"], [])

    def test_invalid_signals_rejected(self):
        self.login("doctor")
        self.assertEqual(self.post_signal({"type": "hack"}).status_code, 400)
        self.assertEqual(self.client.post(self.url("signal"), "nope", content_type="application/json").status_code, 400)
        self.assertEqual(self.post_signal({"type": "offer", "data": "x" * 25000}).status_code, 400)

    def test_no_signaling_when_room_not_open(self):
        closed = room_for(self._appointment(timedelta(days=4)))
        self.login("doctor")
        self.client.post(self.url("signal", closed), json.dumps({"type": "hello"}), content_type="application/json")
        self.login("patient")
        self.assertEqual(self.client.get(self.url("signal", closed)).json()["signals"], [])


class FinishTests(RoomTestCase):
    def setUp(self):
        super().setUp()
        self.room.open()

    def test_doctor_writes_report_and_closes(self):
        self.login("doctor")
        response = self.client.post(self.url("finish"), {
            "diagnosis": "Rhume", "observations": "", "recommendations": "Repos et hydratation", "follow_up": "",
        })
        self.assertRedirects(response, self.url("summary"))
        self.room.refresh_from_db()
        self.appointment.refresh_from_db()
        self.assertTrue(self.room.is_closed)
        self.assertEqual(self.appointment.status, Appointment.Status.COMPLETED)
        self.assertEqual(ConsultationSummary.objects.get().author, self.doctor)
        self.assertTrue(Notification.objects.filter(user=self.patient, kind="summary").exists())

        # Patient reads the report; chat is closed.
        self.login("patient")
        self.assertContains(self.client.get(self.url("summary")), "Repos et hydratation")
        self.assertEqual(self.client.post(self.url("chat"), {"body": "Merci"}).status_code, 409)
        self.assertContains(self.client.get(self.url("room")), "Historique des messages")
        self.assertContains(self.client.get(reverse("patient_dashboard")), "Rhume")

    def test_report_fields_required(self):
        self.login("doctor")
        response = self.client.post(self.url("finish"), {"diagnosis": "", "recommendations": ""})
        self.assertEqual(response.status_code, 200)
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_open)

    def test_patient_cannot_write_report(self):
        self.login("patient")
        self.assertEqual(self.client.get(self.url("finish")).status_code, 404)

    def test_summary_404_before_report(self):
        self.login("patient")
        self.assertEqual(self.client.get(self.url("summary")).status_code, 404)


class NotificationTests(RoomTestCase):
    def test_list_open_and_read_all(self):
        n1 = Notification.objects.create(user=self.patient, kind="confirmed", title="A", url="/rendez-vous/")
        Notification.objects.create(user=self.patient, kind="summary", title="B", url="//evil.example.com")
        other = Notification.objects.create(user=self.doctor, kind="request", title="C")
        self.login("patient")
        response = self.client.get(reverse("teleconsult:notifications"))
        self.assertEqual(response.context["unread_notifications"], 2)
        self.assertRedirects(self.client.get(reverse("teleconsult:notification_open", args=[n1.pk])),
                             "/rendez-vous/", fetch_redirect_response=False)
        n1.refresh_from_db()
        self.assertTrue(n1.is_read)
        evil = Notification.objects.get(title="B")
        response = self.client.get(reverse("teleconsult:notification_open", args=[evil.pk]))
        self.assertRedirects(response, reverse("teleconsult:notifications"))
        self.assertEqual(self.client.get(reverse("teleconsult:notification_open", args=[other.pk])).status_code, 404)
        self.client.post(reverse("teleconsult:notifications_read_all"))
        self.assertFalse(self.patient.notifications.filter(is_read=False).exists())

    def test_consultation_lists(self):
        self.login("patient")
        self.assertContains(self.client.get(reverse("teleconsult:list")), "Dr Sarah Benali")
        self.login("doctor")
        self.assertContains(self.client.get(reverse("teleconsult:list")), "Amina Belhadj")


SHARE_MEDIA = tempfile.mkdtemp()
PDF_BYTES = b"%PDF-1.4\n" + b"0" * 500 + b"\n%%EOF\n"


@override_settings(MEDIA_ROOT=SHARE_MEDIA)
class SharedDocumentTests(RoomTestCase):
    def setUp(self):
        super().setUp()
        self.room.open()

    def share(self, content=PDF_BYTES, name="bilan-sanguin.pdf"):
        return self.client.post(self.url("share_document"), {"file": SimpleUploadedFile(name, content)})

    def test_patient_shares_lab_results(self):
        self.login("patient")
        response = self.share()
        self.assertEqual(response.status_code, 200)
        message = response.json()["message"]
        self.assertEqual(message["kind"], "document")
        self.assertEqual(message["document"]["title"], "bilan-sanguin")
        self.assertIsNone(message["document"]["analysis_url"])  # the AI report is for the doctor
        document = MedicalDocument.objects.get()
        self.assertEqual(document.record.patient, self.patient)
        self.assertEqual(document.appointment, self.appointment)
        self.assertEqual(document.kind, "analysis")
        self.assertEqual(DocumentAnalysis.objects.filter(document=document).count(), 1)  # prepared right away

        # The doctor sees it in the chat with the link to the AI report.
        self.login("doctor")
        data = self.client.get(self.url("chat")).json()
        doc_msg = next(m for m in data["messages"] if m["kind"] == "document")
        self.assertEqual(doc_msg["document"]["analysis_url"], reverse("records:document_analysis", args=[document.pk]))

    def test_fake_file_and_closed_room_rejected(self):
        self.login("patient")
        self.assertEqual(self.share(b"MZ this is not a pdf", "x.pdf").status_code, 400)
        ConsultationRoom.objects.filter(pk=self.room.pk).update(status="closed")
        self.assertEqual(self.share().status_code, 409)

    def test_analysis_page_is_for_following_doctors_only(self):
        self.login("patient")
        self.share()
        document = MedicalDocument.objects.get()
        url = reverse("records:document_analysis", args=[document.pk])
        self.assertNotEqual(self.client.get(url).status_code, 200)  # patient: redirected away
        self.login("doctor")
        response = self.client.get(url)
        self.assertContains(response, "Compte rendu IA du document")
        self.assertContains(response, "désactivée")  # no OpenAI call during tests
        self.client.post(url)
        self.assertEqual(document.analyses.count(), 2)  # re-run
        make_doctor("autre@test.fr", number="ORD-9")
        self.client.login(username="autre@test.fr", password=PASSWORD)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_read_with_ai_button_endpoint(self):
        self.login("patient")
        message = self.share().json()["message"]
        self.assertIsNone(message["document"]["analysis_api"])  # no button for the patient
        document = MedicalDocument.objects.get()
        api = reverse("records:document_analysis_api", args=[document.pk])
        self.assertNotEqual(self.client.post(api).status_code, 200)

        self.login("doctor")
        doc = next(m for m in self.client.get(self.url("chat")).json()["messages"] if m["kind"] == "document")
        self.assertEqual(doc["document"]["analysis_api"], api)
        data = self.client.get(api).json()
        self.assertEqual(data["status"], "error")  # AI is off during tests
        self.assertIn("report_url", data)
        self.client.post(api)  # failed reading: the button starts a new one
        self.assertEqual(document.analyses.count(), 2)
