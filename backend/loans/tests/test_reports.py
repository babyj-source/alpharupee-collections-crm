from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import AuditLog
from loans.models import Activity, Loan, PriorityConfig, ScriptSection
from loans.priority import DEFAULT_WEIGHTS, clean_weights, score_loan
from loans.tests.test_workflow import Base
from messaging.models import MessageLog, WhatsappMessage, WhatsappTemplate
from messaging.tests.test_whatsapp import ON, Resp


class WeightTests(Base):
    def test_defaults_unchanged_and_everyone_head_can_read(self):
        r = self.api(self.head).get("/api/priority/weights").json()
        self.assertEqual((r["weights"], r["changed"]), (DEFAULT_WEIGHTS, False))
        self.assertEqual(self.api(self.a1).get("/api/priority/weights").status_code, 403)

    def test_only_admin_can_change_and_the_list_follows(self):
        admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        self.assertEqual(self.api(self.head).put("/api/priority/weights", {"weights": {"never_called": 40}}, format="json").status_code, 403)
        r = self.api(admin).put("/api/priority/weights", {"weights": {"never_called": 40}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["weights"]["never_called"], r.json()["changed"]), (40, True))
        top = self.api(self.head).get("/api/priority").json()["results"]
        self.assertEqual(next(x["score"] for x in top if x["account_id"] == "L1"), 40 + 25)      # 5 DPD band 25 + 40 never called
        self.assertTrue(AuditLog.objects.filter(action="priority_weights_changed").exists())
        r = self.api(admin).put("/api/priority/weights", {"reset": True}, format="json").json()
        self.assertEqual((r["weights"], r["changed"]), (DEFAULT_WEIGHTS, False))

    def test_bad_values_are_refused(self):
        for bad in ({"nope": 1}, {"never_called": "x"}, {"never_called": 9999}, {"stale_days": 0}, {"dpd_points": [[7, 1], [0, 2]]},
                    {"dpd_points": [[0, 1], [0, 2]]}, {"dpd_points": "x"}, {"amount_full_at": True}):
            self.assertIsNotNone(clean_weights(bad)[1], bad)
        self.assertEqual(clean_weights({"dpd_points": [[0, 5], [30, 50]]})[1], "")

    def test_score_uses_changed_points(self):
        PriorityConfig.objects.create(pk=1, values={"dpd_points": [[1000000, 1]], "never_called": 0})
        loan = Loan.objects.get(pk=self.l1.pk)
        pts, _ = score_loan(loan, timezone.now(), timezone.localdate())
        self.assertEqual(pts, 1)


class PerformanceTests(Base):
    def setUp(self):
        super().setUp()
        for code in ("ptp_given", "no_answer", "refuses"):
            Activity.objects.create(loan=self.l1, user=self.a1, kind="disposition", disposition=code)
        Activity.objects.create(loan=self.l2, user=self.a2, kind="disposition", disposition="no_answer")
        Activity.objects.create(loan=self.l1, user=self.a1, kind="call", detail="Call placed to borrower")
        MessageLog.objects.create(loan=self.l1, template_key="t", msg91_template_id="1", state="sent", sent_by=self.a1)
        WhatsappMessage.objects.create(loan=self.l1, direction="out", kind="template", state="sent", sent_by=self.a1)
        WhatsappMessage.objects.create(loan=self.l1, direction="out", kind="template", state="failed", sent_by=self.a1)

    def test_head_sees_every_agent_with_extras(self):
        r = self.api(self.head).get("/api/performance?days=7").json()
        row = next(x for x in r["rows"] if x["agent_id"] == self.a1.pk)
        self.assertEqual((row["calls"], row["connected"], row["sms_sent"], row["whatsapp_sent"], row["calls_placed"]), (3, 2, 1, 1, 1))
        self.assertEqual(len(r["rows"]), 2)
        self.assertEqual(r["totals"]["calls"], 4)
        self.assertIsNone(r["detail"])

    def test_head_can_drill_into_one_agent(self):
        d = self.api(self.head).get(f"/api/performance?agent={self.a2.pk}").json()["detail"]
        self.assertEqual((d["agent"], sum(x["calls"] for x in d["daily"])), ("Agent Two", 1))
        self.assertEqual(next(o["count"] for o in d["outcomes"] if o["code"] == "no_answer"), 1)

    def test_agent_sees_only_their_own_even_when_asking_for_another(self):
        r = self.api(self.a1).get(f"/api/performance?agent={self.a2.pk}").json()
        self.assertEqual([x["agent_id"] for x in r["rows"]], [self.a1.pk])
        self.assertEqual((r["mine"], r["detail"]["agent"], r["totals"]), (True, "Agent One", None))

    def test_needs_login(self):
        self.assertIn(self.api().get("/api/performance").status_code, (401, 403))


class AuditTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        AuditLog.objects.create(user=self.a1, action="mobile_revealed", object_type="loan", object_id="5", detail="borrower")
        AuditLog.objects.create(user=self.a2, action="call_placed", object_type="loan", object_id="6", detail="borrower")

    def test_admin_only(self):
        for u in (self.a1, self.head):
            self.assertEqual(self.api(u).get("/api/audit").status_code, 403)

    def test_filters_and_actions_list(self):
        r = self.api(self.admin).get("/api/audit?action=call_placed").json()
        self.assertEqual([x["who"] for x in r["results"]], ["Agent Two"])
        self.assertIn("mobile_revealed", r["actions"])
        r = self.api(self.admin).get("/api/audit?q=Agent One").json()
        self.assertEqual([x["action"] for x in r["results"]], ["mobile_revealed"])
        self.assertEqual(self.api(self.admin).get("/api/audit?days=x").status_code, 400)

    def test_pages(self):
        for i in range(60):
            AuditLog.objects.create(user=self.a1, action="loan_updated", detail=str(i))
        r = self.api(self.admin).get("/api/audit?page=2").json()
        self.assertEqual((r["count"], len(r["results"])), (62, 12))


class ScriptTests(Base):
    def test_everyone_reads_in_order_and_admin_edits(self):
        r = self.api(self.a1).get("/api/scripts").json()
        self.assertEqual([s["key"] for s in r], ["opening", "objections", "payment", "dos_donts"])
        admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        self.assertEqual(self.api(self.head).put("/api/scripts/opening", {"body": "x"}, format="json").status_code, 403)
        r = self.api(admin).put("/api/scripts/opening", {"body": "Hello {name}", "title": "Open"}, format="json")
        self.assertEqual((r.status_code, r.json()["title"]), (200, "Open"))
        self.assertEqual(ScriptSection.objects.get(key="opening").body, "Hello {name}")
        self.assertEqual(self.api(admin).put("/api/scripts/zzz", {"body": "x"}, format="json").status_code, 404)
        self.assertEqual(self.api(admin).put("/api/scripts/opening", {"title": " "}, format="json").status_code, 400)
        self.assertTrue(AuditLog.objects.filter(action="script_changed").exists())


@ON
@override_settings(WHATSAPP_MAX_PER_LOAN_PER_DAY=3)
class SentLogAndAutoTests(Base):
    def setUp(self):
        super().setUp()
        Loan.objects.filter(pk=self.l2.pk).update(closure_amount=Decimal("4500"), borrower_name="RAVI KUMAR",
                                                  oldest_due_date=timezone.localdate() - timezone.timedelta(days=45))
        Loan.objects.filter(pk=self.l1.pk).update(crm_status="dispute")      # keeps L2 the only account the automatic run can pick
        WhatsappTemplate.objects.filter(key="wa_overdue").update(header_image_url="https://img.example/a.jpg")
        for p in (mock.patch("messaging.service.in_window", return_value=True), mock.patch("messaging.management.commands.auto_whatsapp.in_window", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def run_cmd(self, *a):
        out = StringIO()
        with mock.patch("messaging.msg91.requests.post", return_value=Resp()) as post:
            call_command("auto_whatsapp", *a, stdout=out)
        return out.getvalue(), post

    def test_sent_log_shows_both_channels_and_hides_numbers(self):
        MessageLog.objects.create(loan=self.l1, template_key="t", msg91_template_id="1", state="failed", error="DLT", mobile_last4="1111", sent_by=self.a1)
        WhatsappMessage.objects.create(loan=self.l2, direction="out", kind="template", state="read", mobile_last4="2222", sent_by=None, text="hi")
        WhatsappMessage.objects.create(loan=self.l2, direction="in", kind="text", state="received", text="reply")
        sms = self.api(self.head).get("/api/messaging/sent?channel=sms").json()
        self.assertEqual((sms["count"], sms["failed"], sms["results"][0]["to"], sms["results"][0]["by"]), (1, 1, "xxxxxx1111", "Agent One"))
        wa = self.api(self.head).get("/api/messaging/sent").json()
        self.assertEqual((wa["channel"], wa["count"], wa["results"][0]["by"], wa["results"][0]["account_id"]), ("whatsapp", 1, "Automatic", "L2"))
        self.assertEqual(self.api(self.head).get("/api/messaging/sent?q=ravi").json()["count"], 1)
        self.assertEqual(self.api(self.head).get("/api/messaging/sent?state=failed").json()["count"], 0)
        self.assertEqual(self.api(self.a1).get("/api/messaging/sent").status_code, 403)

    def test_nothing_happens_unless_a_template_is_switched_to_automatic(self):
        out, post = self.run_cmd()
        self.assertIn("not switched on", out)
        self.assertEqual(post.call_count, 0)

    def test_automatic_send_goes_once_a_day_to_eligible_accounts_only(self):
        WhatsappTemplate.objects.filter(key="wa_overdue").update(auto_send=True)
        out, post = self.run_cmd("--dry-run")
        self.assertIn("would send 1", out)               # L2 only: L1 is in dispute, L3 owes nothing, L4 is closed
        self.assertEqual(post.call_count, 0)
        out, post = self.run_cmd()
        self.assertIn("sent 1", out)
        self.assertEqual(post.call_count, 1)
        m = WhatsappMessage.objects.get(loan=self.l2, direction="out")
        self.assertEqual((m.state, m.sent_by, m.template_key), ("sent", None, "wa_overdue"))
        out, post = self.run_cmd()                         # again the same day: nobody left
        self.assertIn("sent 0", out)
        self.assertEqual(post.call_count, 0)

    def test_promise_to_come_callback_dispute_and_paid_are_skipped(self):
        WhatsappTemplate.objects.filter(key="wa_overdue").update(auto_send=True)
        from loans.models import Ptp
        Ptp.objects.create(loan=self.l2, agent=self.a2, promised_date=timezone.localdate() + timezone.timedelta(days=2), amount=100, reason="forgot")
        self.assertIn("sent 0", self.run_cmd()[0])
        Ptp.objects.all().delete()
        Loan.objects.filter(pk=self.l2.pk).update(callback_at=timezone.now() + timezone.timedelta(hours=3))
        self.assertIn("sent 0", self.run_cmd()[0])
        Loan.objects.filter(pk=self.l2.pk).update(callback_at=None, crm_status="dispute")
        self.assertIn("sent 0", self.run_cmd()[0])

    def test_outside_calling_hours_and_when_whatsapp_is_off(self):
        WhatsappTemplate.objects.filter(key="wa_overdue").update(auto_send=True)
        with mock.patch("messaging.management.commands.auto_whatsapp.in_window", return_value=False):
            self.assertIn("Outside calling hours", self.run_cmd()[0])
        with override_settings(MSG91_WHATSAPP_ENABLED=False):
            self.assertIn("switched off", self.run_cmd()[0])

    def test_stops_after_repeated_failures(self):
        WhatsappTemplate.objects.filter(key="wa_overdue").update(auto_send=True)
        for i in range(7):
            Loan.objects.create(account_id=f"X{i}", operational_status="0", dpd=10 + i, demand_due=100, borrower_mobile=f"91988888{i:04d}", borrower_name="A B",
                                oldest_due_date=timezone.localdate() - timezone.timedelta(days=10), closure_amount=Decimal("500"))
        out = StringIO()
        with mock.patch("messaging.msg91.requests.post", return_value=Resp(200, {"status": "failed", "message": "bad"})):
            call_command("auto_whatsapp", stdout=out)
        self.assertIn("Stopped early", out.getvalue())
        self.assertEqual(WhatsappMessage.objects.filter(state="failed").count(), 5)

    def test_admin_can_switch_a_template_to_automatic(self):
        admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        r = self.api(admin).put("/api/messaging/whatsapp/wa_overdue", {"auto_send": True}, format="json")
        self.assertEqual((r.status_code, r.json()["auto_send"]), (200, True))
        self.assertEqual(self.api(self.head).put("/api/messaging/whatsapp/wa_overdue", {"auto_send": False}, format="json").status_code, 403)
