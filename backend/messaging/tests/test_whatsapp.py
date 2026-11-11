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


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
PDF = b"%PDF-1.4\n" + b"x" * 40


def fetched(data=PNG, mime="image/png"):
    return mock.patch("messaging.media.download", return_value=(data, mime))


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
        m = WhatsappMessage.objects.get()
        self.assertEqual((m.kind, m.media_type, m.text, m.media_url), ("media", "image", "", ""))      # no link from MSG91: nothing to download
        row = self.api(self.a2).get(f"/api/messaging/whatsapp/chat?loan={self.l2.pk}").json()["messages"][0]
        self.assertEqual((row["media"]["type"], row["media"]["ready"]), ("image", False))

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


@ON
class InboundFileTests(Wa):
    URL = "/api/messaging/whatsapp/webhook?token=s3cret"

    def hook(self, body):
        return self.api().post(self.URL, json.dumps(body), content_type="application/json")

    def test_photo_is_kept_and_shown_to_the_agent_who_owns_the_account(self):
        body = {"customerNumber": "919222222222", "contentType": "image", "uuid": "p-1",
                "image": json.dumps({"url": "https://media.example/a.jpg", "mime_type": "image/jpeg", "caption": "my receipt"})}
        with fetched(b"\xff\xd8\xff\xe0" + b"j" * 30, "image/jpeg") as dl:
            self.hook(body)
        dl.assert_called_once_with("https://media.example/a.jpg")
        m = WhatsappMessage.objects.get()
        self.assertEqual((m.kind, m.media_type, m.text, m.media_mime, m.state), ("media", "image", "my receipt", "image/jpeg", "received"))
        r = self.api(self.a2).get(f"/api/messaging/whatsapp/media/{m.pk}")
        self.assertEqual((r.status_code, r["Content-Type"], r["Content-Disposition"].split(";")[0]), (200, "image/jpeg", "inline"))
        self.assertTrue(r.content.startswith(b"\xff\xd8\xff"))
        self.assertEqual(self.api(self.a1).get(f"/api/messaging/whatsapp/media/{m.pk}").status_code, 404)      # not their account
        self.assertEqual(self.api().get(f"/api/messaging/whatsapp/media/{m.pk}").status_code, 403)
        row = self.api(self.a2).get(f"/api/messaging/whatsapp/chat?loan={self.l2.pk}").json()["messages"][0]
        self.assertEqual((row["text"], row["media"]["type"], row["media"]["url"], row["media"]["ready"]), ("my receipt", "image", f"/api/messaging/whatsapp/media/{m.pk}", True))

    def test_file_is_fetched_when_the_chat_asks_if_the_webhook_could_not(self):
        with fetched() as dl:
            dl.return_value = None
            self.hook({"customerNumber": "919222222222", "contentType": "image", "image": {"link": "https://media.example/b.png"}, "uuid": "p-2"})
        m = WhatsappMessage.objects.get()
        self.assertEqual(m.media_url, "https://media.example/b.png")
        with fetched():
            r = self.api(self.a2).get(f"/api/messaging/whatsapp/media/{m.pk}")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        with mock.patch("messaging.media.download", side_effect=AssertionError("fetched twice")):
            self.assertEqual(self.api(self.a2).get(f"/api/messaging/whatsapp/media/{m.pk}").status_code, 200)       # kept after the first time

    def test_document_keeps_its_name_and_is_not_run_in_the_page(self):
        with fetched(b"<html><script>alert(1)</script></html>", "text/html"):
            self.hook({"customerNumber": "919222222222", "contentType": "document", "uuid": "d-1",
                       "document": {"url": "https://media.example/x", "filename": "../../salary slip.pdf", "caption": "slip"}})
        m = WhatsappMessage.objects.get()
        self.assertEqual((m.media_type, m.media_name, m.text), ("document", "salary slip.pdf", "slip"))
        r = self.api(self.a2).get(f"/api/messaging/whatsapp/media/{m.pk}")
        self.assertEqual((r["Content-Type"], r["Content-Disposition"].split(";")[0]), ("application/octet-stream", "attachment"))
        self.assertIn("sandbox", r["Content-Security-Policy"])

    def test_button_replies_show_what_the_customer_tapped(self):
        self.hook({"customerNumber": "919222222222", "contentType": "button", "uuid": "b-1", "button": json.dumps({"payload": "p1", "text": "Pay now"})})
        self.hook({"customerNumber": "919222222222", "contentType": "interactive", "uuid": "b-2",
                   "interactive": {"type": "button_reply", "button_reply": {"id": "x", "title": "I need more time"}}})
        self.hook({"customerNumber": "919222222222", "contentType": "interactive", "uuid": "b-3",
                   "interactive": {"type": "list_reply", "list_reply": {"id": "y", "title": "Next week"}}})
        self.assertEqual([(m.kind, m.text) for m in WhatsappMessage.objects.order_by("id")],
                         [("button", "Pay now"), ("button", "I need more time"), ("button", "Next week")])

    def test_text_sent_as_a_json_string_is_unwrapped_and_a_location_is_described(self):
        self.hook({"customerNumber": "919222222222", "contentType": "text", "uuid": "t-1", "text": json.dumps({"body": "Hello there"})})
        self.hook({"customerNumber": "919222222222", "contentType": "location", "uuid": "l-1", "location": {"latitude": 12.97, "longitude": 77.59}})
        self.hook({"customerNumber": "919222222222", "contentType": "sticker_pack", "uuid": "o-1"})
        self.assertEqual([m.text for m in WhatsappMessage.objects.order_by("id")],
                         ["Hello there", "Shared a location (12.97, 77.59)", "Sent a sticker_pack message that the CRM cannot show. Open it in WhatsApp."])

    def test_inbox_names_a_photo_that_has_no_caption(self):
        with fetched():
            self.hook({"customerNumber": "919222222222", "contentType": "image", "image": {"url": "https://media.example/c.png"}, "uuid": "p-3"})
        self.assertEqual(self.api(self.a2).get("/api/messaging/whatsapp/inbox").json()[0]["last_text"], "Photo")

    def test_downloads_only_from_the_public_internet(self):
        from messaging import media
        with mock.patch("messaging.media.requests.get") as get:
            self.assertIsNone(media.download("http://media.example/a.png"))                        # not https
            for host in ("127.0.0.1", "169.254.169.254", "10.0.0.5"):
                with mock.patch("messaging.media.socket.getaddrinfo", return_value=[(2, 1, 6, "", (host, 443))]):
                    self.assertIsNone(media.download("https://internal.example/a.png"))
            get.assert_not_called()

    def test_a_redirect_to_a_private_address_is_not_followed(self):
        from messaging import media

        class R:
            status_code, headers = 302, {"Location": "https://evil.example/x"}

            def __enter__(self): return self

            def __exit__(self, *a): return False

        def resolve(host, *a, **k):
            return [(2, 1, 6, "", ("8.8.8.8" if host == "media.example" else "10.1.1.1", 443))]
        with mock.patch("messaging.media.socket.getaddrinfo", side_effect=resolve), mock.patch("messaging.media.requests.get", return_value=R()) as get:
            self.assertIsNone(media.download("https://media.example/a.png"))
        self.assertEqual(get.call_count, 1)


@ON
class AttachmentSendTests(Wa):
    def open_window(self):
        WhatsappMessage.objects.create(loan=self.l2, direction="in", text="hi", state="received", mobile_last4="2222")

    def up(self, user, name="slip.png", data=PNG, text="", loan=None):
        from django.core.files.uploadedfile import SimpleUploadedFile
        body = {"loan": (loan or self.l2).pk, "file": SimpleUploadedFile(name, data), "text": text}
        return self.api(user).post("/api/messaging/whatsapp/chat", body, format="multipart", HTTP_HOST="testserver")

    def test_picture_goes_out_through_a_signed_public_link(self):
        self.open_window()
        with override_settings(PUBLIC_BASE_URL="https://crm.example"), mock.patch("messaging.msg91.requests.post", return_value=Resp()) as post:
            r = self.up(self.a2, text="Your receipt")
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["kind"], d["text"], d["media"]["type"], d["media"]["name"], d["state"]), ("media", "Your receipt", "image", "slip.png", "sent"))
        body = post.call_args.kwargs["json"]
        self.assertEqual((body["content_type"], body["recipient_number"], body["image"]["caption"]), ("image", "919222222222", "Your receipt"))
        link = body["image"]["link"]
        self.assertTrue(link.startswith("https://crm.example/api/messaging/whatsapp/media/"), link)
        path = link.replace("https://crm.example", "")
        got = self.api().get(path)                                       # MSG91 has no login
        self.assertEqual((got.status_code, got["Content-Type"], got.content), (200, "image/png", PNG))
        self.assertEqual(self.api().get(path[:-3] + "abc").status_code, 404)                      # a tampered link
        other = WhatsappMessage.objects.create(loan=self.l2, direction="out", media_type="image", media_mime="image/png")
        self.assertEqual(self.api().get(path.replace(f"/media/{d['id']}/", f"/media/{other.pk}/")).status_code, 404)   # a link is for one file only
        self.assertTrue(AuditLog.objects.filter(action="whatsapp_chat_sent", detail="image: slip.png").exists())

    def test_document_uses_its_own_shape_and_the_flat_one_is_tried_when_refused(self):
        self.open_window()
        refused = Resp(400, {"status": "fail", "message": "bad shape"})
        with override_settings(PUBLIC_BASE_URL="https://crm.example"), mock.patch("messaging.msg91.requests.post", side_effect=[refused, Resp()]) as post:
            r = self.up(self.a2, "Statement.pdf", PDF)
        self.assertEqual((r.status_code, r.json()["state"]), (201, "sent"))
        first, second = (c.kwargs["json"] for c in post.call_args_list)
        self.assertEqual((first["content_type"], first["document"]["filename"]), ("document", "Statement.pdf"))
        self.assertEqual((second["content_type"], second["filename"], second["media_url"] == first["document"]["link"]), ("document", "Statement.pdf", True))

    def test_rules_window_type_size_and_scope(self):
        with override_settings(PUBLIC_BASE_URL="https://crm.example"), mock.patch("messaging.msg91.requests.post") as post:
            self.assertEqual(self.up(self.a2).status_code, 400)                                    # customer never wrote
            self.open_window()
            self.assertEqual(self.up(self.a2, "evil.png", b"<html>").status_code, 400)             # not really a picture
            self.assertEqual(self.up(self.a2, "run.exe", b"MZ" + b"0" * 20).status_code, 400)      # not an allowed kind
            self.assertEqual(self.up(self.a2, "big.png", PNG + b"0" * (5 * 1024 * 1024)).status_code, 400)
            self.assertEqual(self.up(self.a2, "empty.pdf", b"").status_code, 400)
            self.assertEqual(self.up(self.a1).status_code, 404)                                    # not their account
            post.assert_not_called()
        with override_settings(PUBLIC_BASE_URL=""), mock.patch("messaging.msg91.requests.post") as post:
            self.assertEqual(self.up(self.a2).status_code, 400)                                    # a plain http address cannot be fetched by MSG91
            post.assert_not_called()
        self.assertEqual(WhatsappMessage.objects.filter(direction="out").count(), 0)

    def test_msg91_refusal_keeps_the_message_as_failed(self):
        self.open_window()
        with override_settings(PUBLIC_BASE_URL="https://crm.example"), mock.patch("messaging.msg91.requests.post", return_value=Resp(500, {"message": "down"})):
            r = self.up(self.a2)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(WhatsappMessage.objects.get(direction="out").state, "failed")
