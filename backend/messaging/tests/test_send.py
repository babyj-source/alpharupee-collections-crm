from datetime import datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import override_settings

from accounts.models import AuditLog
from loans.tests.test_workflow import Base
from messaging import msg91
from messaging.models import MessageLog, SmsTemplate
from messaging.service import SendRefused, in_window, send_sms_for_loan

LINK = "https://pay.example/abc"
DAY = datetime(2026, 11, 5, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
NIGHT = datetime(2026, 11, 5, 21, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {"type": "success", "message": "req123"}

    def json(self):
        return self._b


ON = override_settings(MSG91_SMS_ENABLED=True, MSG91_AUTHKEY="k", PAYMENT_LINK_DEFAULT=LINK)


class Sms(Base):
    def setUp(self):
        super().setUp()
        type(self.l2).objects.filter(pk=self.l2.pk).update(closure_amount=Decimal("4500"), dpd=45, borrower_name="RAVI KUMAR")
        self.l2.refresh_from_db()
        p = mock.patch("messaging.service.in_window", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def send(self, loan=None, user=None, **kw):
        return send_sms_for_loan(loan or self.l2, user or self.a2, **kw)


@ON
class ServiceTests(Sms):
    def test_sends_the_bucket_template_with_the_days_closure_amount(self):
        with mock.patch("messaging.msg91.requests.post", return_value=FakeResp()) as post:
            m = self.send()
        self.assertEqual((m.state, m.template_key, m.provider_ref), ("sent", "bkt2", "req123"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["template_id"], "6ab6233dd16941664d09be52")
        self.assertEqual(body["recipients"], [{"mobiles": "919222222222", "number": "4500", "url": LINK}])
        self.assertEqual(post.call_args.kwargs["headers"]["authkey"], "k")
        self.assertEqual(m.text, f"Alpha Rupee: Your loan is overdue. Outstanding ₹4500 . Please pay immediately or call +918065075438 or pay now: {LINK}")
        self.assertEqual(m.mobile_last4, "2222")
        self.assertNotIn("9222222222", m.text)

    def test_same_message_twice_in_a_day_is_refused(self):
        with mock.patch("messaging.msg91.requests.post", return_value=FakeResp()):
            self.send()
            with self.assertRaisesMessage(SendRefused, "already sent"):
                self.send()

    def test_msg91_refusal_is_logged_as_failed_and_can_be_retried(self):
        bad = FakeResp(200, {"type": "error", "message": "Template not approved"})
        with mock.patch("messaging.msg91.requests.post", return_value=bad):
            m = self.send()
        self.assertEqual((m.state, m.error), ("failed", "MSG91 refused (HTTP 200): Template not approved"))
        with mock.patch("messaging.msg91.requests.post", return_value=FakeResp()):
            self.assertEqual(self.send().state, "sent")    # a failed attempt does not block the retry

    def test_refusals_send_nothing(self):
        with mock.patch("messaging.msg91.requests.post") as post:
            for kw, msg in (({"link": "http://x"}, "https://"), ({"template_key": "nope"}, "not available")):
                with self.assertRaisesMessage(SendRefused, msg):
                    self.send(**kw)
            type(self.l2).objects.filter(pk=self.l2.pk).update(borrower_mobile="")
            self.l2.refresh_from_db()
            with self.assertRaisesMessage(SendRefused, "valid mobile"):
                self.send()
            type(self.l2).objects.filter(pk=self.l2.pk).update(borrower_mobile="919222222222", crm_status="dispute")
            self.l2.refresh_from_db()
            with self.assertRaisesMessage(SendRefused, "paid or in dispute"):
                self.send()
            post.assert_not_called()
        self.assertEqual(MessageLog.objects.count(), 0)

    @override_settings(PAYMENT_LINK_DEFAULT="")
    def test_no_link_means_no_message(self):
        with self.assertRaisesMessage(SendRefused, "payment link"):
            self.send()

    def test_demand_reminder_is_refused_until_due_date_exists(self):
        with self.assertRaisesMessage(SendRefused, "Cannot build"):
            self.send(template_key="demand")


class SwitchAndWindowTests(Sms):
    def test_off_by_default(self):
        with override_settings(PAYMENT_LINK_DEFAULT=LINK), mock.patch("messaging.msg91.requests.post") as post:
            with self.assertRaisesMessage(SendRefused, "switched off"):
                self.send()
            post.assert_not_called()
        self.assertEqual(MessageLog.objects.count(), 0)

    def test_window_is_08_to_19(self):
        self.assertTrue(in_window(DAY))
        self.assertFalse(in_window(NIGHT))

    @ON
    def test_outside_the_window_nothing_is_sent(self):
        with mock.patch("messaging.service.in_window", return_value=False), mock.patch("messaging.msg91.requests.post") as post:
            with self.assertRaisesMessage(SendRefused, "08:00 and 19:00"):
                self.send()
            post.assert_not_called()


@ON
class ApiTests(Sms):
    def post(self, user, body):
        return self.api(user).post("/api/messaging/sms", body, format="json")

    def test_agent_sends_for_own_loan_only_and_cannot_pick_template_or_link(self):
        with mock.patch("messaging.msg91.requests.post", return_value=FakeResp()) as post:
            r = self.post(self.a2, {"loan": self.l2.pk, "template": "npa", "link": "https://evil.example"})
            self.assertEqual(r.status_code, 201, r.content)
            self.assertEqual(r.json()["template"], "bkt2")                      # template ignored
            self.assertEqual(post.call_args.kwargs["json"]["recipients"][0]["url"], LINK)   # link ignored
            self.assertEqual(self.post(self.a1, {"loan": self.l2.pk}).status_code, 404)  # someone else's loan
        self.assertTrue(AuditLog.objects.filter(action="sms_sent").exists())

    def test_head_may_choose_template_and_link(self):
        with mock.patch("messaging.msg91.requests.post", return_value=FakeResp()) as post:
            r = self.post(self.head, {"loan": self.l2.pk, "template": "npa", "link": "https://pay.example/xyz"})
        self.assertEqual((r.status_code, r.json()["template"]), (201, "npa"))
        self.assertEqual(post.call_args.kwargs["json"]["recipients"][0]["url"], "https://pay.example/xyz")

    def test_provider_failure_is_a_502_and_is_logged(self):
        with mock.patch("messaging.msg91.requests.post", return_value=FakeResp(200, {"type": "error", "message": "blocked"})):
            r = self.post(self.a2, {"loan": self.l2.pk})
        self.assertEqual(r.status_code, 502)
        self.assertTrue(AuditLog.objects.filter(action="sms_failed").exists())

    def test_log_shows_messages_without_the_number(self):
        with mock.patch("messaging.msg91.requests.post", return_value=FakeResp()):
            self.post(self.a2, {"loan": self.l2.pk})
        rows = self.api(self.a2).get(f"/api/messaging/log?loan={self.l2.pk}").json()
        self.assertEqual((len(rows), rows[0]["to"], rows[0]["sent_by"]), (1, "xxxxxx2222", "Agent Two"))
        self.assertEqual(self.api(self.a1).get(f"/api/messaging/log?loan={self.l2.pk}").status_code, 404)
        self.assertEqual(self.api().get(f"/api/messaging/log?loan={self.l2.pk}").status_code, 403)
