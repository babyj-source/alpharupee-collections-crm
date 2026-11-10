import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from accounts.models import AuditLog
from loans.models import Activity
from loans.tests.test_workflow import Base
from messaging.models import WhatsappMessage, WhatsappTemplate, WhatsappWebhookLog
from messaging.service import SendRefused
from messaging.whatsapp import preview_whatsapp, send_chat_text, send_whatsapp_for_loan

LINK = "https://pay.example/a-very-long-link-that-whatsapp-does-not-mind/abc123"
IMG = "https://img.example/missed.jpg"
ON = override_settings(MSG91_WHATSAPP_ENABLED=True, MSG91_AUTHKEY="k", MSG91_WHATSAPP_NUMBER="918065075438", PAYMENT_LINK_DEFAULT=LINK,
                       WHATSAPP_WEBHOOK_TOKEN="s3cret")


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {"status": "success", "data": {"message_uuid": "wamid-1"}}

    def json(self):
        return self._b


class Wa(Base):
    def setUp(self):
        super().setUp()
        type(self.l2).objects.filter(pk=self.l2.pk).update(closure_amount=Decimal("4500"), dpd=45, borrower_name="RAVI KUMAR",
                                                          oldest_due_date=timezone.localdate() - timedelta(days=45))
        self.l2.refresh_from_db()
        WhatsappTemplate.objects.filter(key="wa_overdue").update(header_image_url=IMG)
        # WhatsApp sending imports in_window by name, so it has to be switched off in both modules.
        for target in ("messaging.service.in_window", "messaging.whatsapp.in_window"):
            p = mock.patch(target, return_value=True)
            p.start()
            self.addCleanup(p.stop)

    def post(self, user, path, body):
        return self.api(user).post(f"/api/messaging/whatsapp/{path}", body, format="json")


@ON
class TemplateSendTests(Wa):
    def test_overdue_template_goes_out_in_msg91_shape(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()) as post:
            m = send_whatsapp_for_loan(self.l2, self.a2)
        self.assertEqual((m.state, m.template_key, m.provider_ref, m.mobile_last4), ("sent", "wa_overdue", "wamid-1", "2222"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["integrated_number"], "918065075438")
        t = body["payload"]["template"]
        self.assertEqual((t["name"], t["namespace"], t["language"]["code"]), ("repay_overdue_missed", "544dead2_efd4_43d5_a69a_1ec9b1dbeafc", "en"))
        comp = t["to_and_components"][0]
        self.assertEqual(comp["to"], ["919222222222"])
        self.assertEqual(comp["components"]["header_1"], {"type": "image", "value": IMG})
        self.assertEqual([comp["components"][f"body_{i}"]["value"] for i in (1, 2, 3, 4)],
                         ["Ravi", (timezone.localdate() - timedelta(days=45)).strftime("%d-%b-%Y"), "4500", LINK])
        self.assertIn("Outstanding Amount: ₹4500", m.text)
        self.assertEqual(post.call_args.kwargs["headers"]["authkey"], "k")
        self.assertTrue(Activity.objects.filter(loan=self.l2, kind="message", detail="WhatsApp sent to the borrower: repay_overdue_missed").exists())

    def test_image_template_needs_a_header_image(self):
        WhatsappTemplate.objects.filter(key="wa_overdue").update(header_image_url="")
        with self.assertRaisesMessage(SendRefused, "header image"):
            send_whatsapp_for_loan(self.l2, self.a2)

    def test_due_date_reminders_cannot_be_picked_for_an_account_not_yet_overdue(self):
        type(self.l2).objects.filter(pk=self.l2.pk).update(dpd=0)
        self.l2.refresh_from_db()
        with self.assertRaisesMessage(SendRefused, "No WhatsApp template fits"):
            send_whatsapp_for_loan(self.l2, self.a2)

    def test_repeat_needs_confirmation_and_cap_holds_and_failures_do_not_count(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp(200, {"status": "failed", "message": "bad"})):
            self.assertEqual(send_whatsapp_for_loan(self.l2, self.a2).state, "failed")
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()) as post:
            send_whatsapp_for_loan(self.l2, self.a2)                                   # the failed one did not block this
            with self.assertRaisesMessage(SendRefused, "already sent to this borrower today at"):
                send_whatsapp_for_loan(self.l2, self.a2)
            send_whatsapp_for_loan(self.l2, self.a2, again=True)
            send_whatsapp_for_loan(self.l2, self.a2, again=True)                       # the third that went out today
            with self.assertRaisesMessage(SendRefused, "already had 3 WhatsApp"):
                send_whatsapp_for_loan(self.l2, self.a2, again=True)
            self.assertEqual(post.call_count, 3)

    def test_preview_shows_text_and_warning(self):
        r = preview_whatsapp(self.l2)
        self.assertEqual((r["template"], r["problem"], r["warning"], r["image"]), ("wa_overdue", "", "", True))
        self.assertIn(LINK, r["text"])
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()):
            send_whatsapp_for_loan(self.l2, self.a2)
        self.assertIn("already sent", preview_whatsapp(self.l2)["warning"])

    @override_settings(MSG91_WHATSAPP_ENABLED=False)
    def test_off_by_default(self):
        with mock.patch("messaging.msg91.requests.post") as post:
            with self.assertRaisesMessage(SendRefused, "switched off"):
                send_whatsapp_for_loan(self.l2, self.a2)
            post.assert_not_called()


@ON
class ChatTests(Wa):
    def inbound(self, minutes_ago=5, text="I will pay tomorrow"):
        m = WhatsappMessage.objects.create(loan=self.l2, direction="in", text=text, state="received", seen=False, mobile_last4="2222")
        WhatsappMessage.objects.filter(pk=m.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
        return m

    def test_free_text_only_inside_the_24_hour_window(self):
        with self.assertRaisesMessage(SendRefused, "Send a template instead"):
            send_chat_text(self.l2, self.a2, "hello")                                   # the customer never wrote
        self.inbound(minutes_ago=60 * 25)
        with self.assertRaisesMessage(SendRefused, "Send a template instead"):
            send_chat_text(self.l2, self.a2, "hello")                                   # window closed
        self.inbound(minutes_ago=10)
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()) as post:
            m = send_chat_text(self.l2, self.a2, "  Thank you, noted.  ")
        self.assertEqual((m.state, m.kind, m.text), ("sent", "text", "Thank you, noted."))
        self.assertEqual(post.call_args.kwargs["json"], {"integrated_number": "918065075438", "recipient_number": "919222222222",
                                                         "content_type": "text", "text": "Thank you, noted."})

    def test_chat_api_thread_marks_read_and_scopes(self):
        self.inbound()
        c = self.api(self.a2)
        r = c.get(f"/api/messaging/whatsapp/chat?loan={self.l2.pk}").json()
        self.assertEqual([(m["direction"], m["by"]) for m in r["messages"]], [("in", "Customer")])
        self.assertTrue(r["window_open"])
        self.assertFalse(WhatsappMessage.objects.filter(direction="in", seen=False).exists())      # opening it marks it read
        self.assertEqual(self.api(self.a1).get(f"/api/messaging/whatsapp/chat?loan={self.l2.pk}").status_code, 404)
        self.assertEqual(self.api().get(f"/api/messaging/whatsapp/chat?loan={self.l2.pk}").status_code, 403)

    def test_chat_api_post(self):
        self.inbound()
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()):
            r = self.post(self.a2, "chat", {"loan": self.l2.pk, "text": "Hello"})
        self.assertEqual((r.status_code, r.json()["by"]), (201, "Agent Two"))
        self.assertEqual(self.post(self.a2, "chat", {"loan": self.l2.pk, "text": "  "}).status_code, 400)
        self.assertEqual(self.post(self.a1, "chat", {"loan": self.l2.pk, "text": "x"}).status_code, 404)
        self.assertTrue(AuditLog.objects.filter(action="whatsapp_chat_sent").exists())

    def test_inbox_lists_conversations_with_unread_counts(self):
        self.inbound(minutes_ago=30, text="first")
        self.inbound(minutes_ago=3, text="second")
        rows = self.api(self.a2).get("/api/messaging/whatsapp/inbox").json()
        self.assertEqual([(r["account_id"], r["last_text"], r["unread"]) for r in rows], [("L2", "second", 2)])
        self.assertEqual(self.api(self.a1).get("/api/messaging/whatsapp/inbox").json(), [])      # not their account

    def test_template_send_api_rules_for_agents_and_heads(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()) as post:
            r = self.post(self.a2, "send", {"loan": self.l2.pk, "template": "wa_due1", "link": "https://evil.example"})
            self.assertEqual((r.status_code, r.json()["kind"]), (201, "template"))
            self.assertEqual(WhatsappMessage.objects.get().template_key, "wa_overdue")       # the agent's template choice is ignored
            self.assertEqual(self.post(self.a1, "send", {"loan": self.l2.pk}).status_code, 404)
            self.assertEqual(self.post(self.a2, "send", {"loan": self.l2.pk}).status_code, 400)  # repeat without confirmation
            self.assertEqual(post.call_count, 1)

    def test_meta_flag(self):
        self.assertTrue(self.api(self.a2).get("/api/meta").json()["whatsapp_enabled"])
        with override_settings(MSG91_WHATSAPP_NUMBER=""):
            self.assertFalse(self.api(self.a2).get("/api/meta").json()["whatsapp_enabled"])


@ON
class WebhookTests(Wa):
    URL = "/api/messaging/whatsapp/webhook?token=s3cret"

    def hook(self, body, url=None):
        return self.api().post(url or self.URL, json.dumps(body), content_type="application/json")

    def test_token_is_required(self):
        self.assertEqual(self.hook({"a": 1}, "/api/messaging/whatsapp/webhook").status_code, 403)
        self.assertEqual(self.hook({"a": 1}, "/api/messaging/whatsapp/webhook?token=wrong").status_code, 403)
        with override_settings(WHATSAPP_WEBHOOK_TOKEN=""):
            self.assertEqual(self.hook({"a": 1}, "/api/messaging/whatsapp/webhook?token=").status_code, 403)   # no token set: closed
        self.assertEqual(WhatsappWebhookLog.objects.count(), 0)

    def test_inbound_message_is_linked_to_the_loan(self):
        r = self.hook({"customerNumber": "919222222222", "contentType": "text", "text": "Will pay on Friday", "uuid": "in-1", "direction": "IN"})
        self.assertEqual(r.status_code, 200)
        m = WhatsappMessage.objects.get()
        self.assertEqual((m.loan_id, m.direction, m.text, m.state, m.seen, m.mobile_last4), (self.l2.pk, "in", "Will pay on Friday", "received", False, "2222"))
        self.assertEqual(WhatsappWebhookLog.objects.get().handled, "inbound")
        self.assertNotIn("919222222222", WhatsappWebhookLog.objects.get().body)                  # numbers are masked in the log

    def test_the_same_message_delivered_twice_is_stored_once(self):
        body = {"customerNumber": "919222222222", "text": "hi", "uuid": "in-9"}
        self.hook(body)
        self.hook(body)
        self.assertEqual(WhatsappMessage.objects.filter(direction="in").count(), 1)

    def test_nested_and_alternative_shapes_are_understood(self):
        self.hook({"data": {"from": "9222222222", "message": {"body": "ok thanks"}, "message_id": "in-2"}})
        self.assertEqual(WhatsappMessage.objects.get().text, "ok thanks")

    def test_media_without_text_is_noted(self):
        self.hook({"customerNumber": "919222222222", "contentType": "image", "uuid": "in-3"})
        self.assertEqual(WhatsappMessage.objects.get().text, "[image]")

    def test_unknown_number_is_kept_unlinked_and_our_own_echo_is_ignored(self):
        self.hook({"customerNumber": "919888800000", "text": "who is this", "uuid": "in-4"})
        self.assertIsNone(WhatsappMessage.objects.get().loan)
        self.hook({"customerNumber": "919222222222", "text": "our own", "uuid": "out-1", "direction": "OUT"})
        self.assertEqual(WhatsappMessage.objects.count(), 1)

    def test_delivery_status_updates_the_sent_message(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()):
            m = send_whatsapp_for_loan(self.l2, self.a2)
        self.hook({"status": "delivered", "message_uuid": "wamid-1"})
        m.refresh_from_db()
        self.assertEqual(m.state, "delivered")
        self.hook({"status": "read", "message_uuid": "wamid-1"})
        m.refresh_from_db()
        self.assertEqual(m.state, "read")
        self.hook({"status": "failed", "message_uuid": "wamid-1", "reason": "Number not on WhatsApp"})
        m.refresh_from_db()
        self.assertEqual((m.state, m.error), ("failed", "Number not on WhatsApp"))

    def test_garbage_is_accepted_and_logged_not_an_error(self):
        r = self.api().post(self.URL, "not json", content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WhatsappWebhookLog.objects.get().handled, "ignored")

    def test_log_is_admin_only_and_pruned(self):
        for i in range(205):
            self.hook({"n": i})
        self.assertEqual(WhatsappWebhookLog.objects.count(), 200)
        admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        self.assertEqual(self.api(self.head).get("/api/messaging/whatsapp/webhook-log").status_code, 403)
        self.assertEqual(len(self.api(admin).get("/api/messaging/whatsapp/webhook-log").json()), 30)
