from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from accounts.models import AuditLog
from loans.models import Activity, Loan
from messaging.tests.test_whatsapp import ON, Resp, Wa

CALLS = override_settings(WHATSAPP_CALL_ENABLED=True)


@CALLS
class WhatsappCallLinkTests(Wa):
    def setUp(self):
        super().setUp()
        p = mock.patch("dialer.views.in_window", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_agent_gets_a_wa_me_link_and_it_is_logged_and_audited(self):
        r = self.post(self.a2, "call", {"loan_id": self.l2.pk, "which": "borrower"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {"url": "https://wa.me/919222222222"})
        self.assertTrue(Activity.objects.filter(loan=self.l2, kind="call", detail="WhatsApp call opened to borrower").exists())
        self.assertTrue(AuditLog.objects.filter(action="whatsapp_call_opened", object_id=str(self.l2.pk), detail="borrower").exists())

    def test_reference_number_works(self):
        r = self.post(self.a1, "call", {"loan_id": self.l1.pk, "which": "ref1"})
        self.assertEqual(r.json(), {"url": "https://wa.me/919333333333"})

    def test_agent_cannot_use_another_agents_account(self):
        self.assertEqual(self.post(self.a1, "call", {"loan_id": self.l2.pk}).status_code, 404)

    def test_closed_account_is_refused(self):
        Loan.objects.filter(pk=self.l2.pk).update(operational_status="2")
        r = self.post(self.a2, "call", {"loan_id": self.l2.pk})
        self.assertEqual(r.status_code, 400)
        self.assertIn("closed", r.json()["detail"])

    def test_missing_number_is_refused(self):
        r = self.post(self.a2, "call", {"loan_id": self.l2.pk, "which": "ref2"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no valid mobile", r.json()["detail"])

    def test_outside_calling_hours_is_refused(self):
        with mock.patch("dialer.views.in_window", return_value=False):
            r = self.post(self.a2, "call", {"loan_id": self.l2.pk})
        self.assertEqual(r.status_code, 400)
        self.assertIn("between", r.json()["detail"])

    def test_needs_login(self):
        self.assertIn(self.api().post("/api/messaging/whatsapp/call", {"loan_id": self.l2.pk}, format="json").status_code, (401, 403))

    @override_settings(WHATSAPP_CALL_ENABLED=False)
    def test_switched_off_by_default(self):
        r = self.post(self.a2, "call", {"loan_id": self.l2.pk})
        self.assertEqual(r.status_code, 400)
        self.assertIn("switched off", r.json()["detail"])
        self.assertFalse(self.api(self.a2).get("/api/meta").json()["whatsapp_call_enabled"])

    def test_meta_flag_on(self):
        self.assertTrue(self.api(self.a2).get("/api/meta").json()["whatsapp_call_enabled"])


@ON
class CallSettingsCommandTests(Wa):
    def run_cmd(self, *args):
        out = StringIO()
        call_command("whatsapp_calling", *args, stdout=out)
        return out.getvalue()

    def test_enable_posts_the_documented_body_with_the_number(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp(200, {"message": "Call settings updated successfully"})) as post:
            out = self.run_cmd("enable")
        self.assertIn("HTTP 200", out)
        self.assertEqual(post.call_args.kwargs["json"], {"integrated_number": "918065075438", "status": "ENABLED", "call_hours": {"status": "DISABLED"}})
        self.assertEqual(post.call_args.kwargs["headers"]["authkey"], "k")
        self.assertTrue(post.call_args.args[0].endswith("/whatsapp/integrated-number/call-settings/"))

    def test_hours_flag_sets_8_to_7_every_day(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp(200, {"message": "ok"})) as post:
            self.run_cmd("enable", "--hours")
        h = post.call_args.kwargs["json"]["call_hours"]
        self.assertEqual((h["status"], h["timezone_id"], len(h["weekly_operating_hours"])), ("ENABLED", "Asia/Kolkata", 7))
        self.assertEqual(h["weekly_operating_hours"][0], {"day_of_week": "MONDAY", "open_time": "0800", "close_time": "1900"})

    def test_disable(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp(200, {"message": "ok"})) as post:
            self.run_cmd("disable")
        self.assertEqual(post.call_args.kwargs["json"]["status"], "DISABLED")

    def test_msg91_refusal_is_shown_and_fails_the_command(self):
        with mock.patch("messaging.msg91.requests.post", return_value=Resp(400, {"message": "Integrated number missing"})):
            with self.assertRaisesMessage(CommandError, "did not accept"):
                self.run_cmd("enable")

    @override_settings(MSG91_WHATSAPP_NUMBER="")
    def test_needs_number_and_key(self):
        with self.assertRaisesMessage(CommandError, "must be set"):
            self.run_cmd("enable")
