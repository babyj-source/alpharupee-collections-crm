from decimal import Decimal


from accounts.models import AuditLog
from loans.tests.test_workflow import Base
from messaging.models import SmsTemplate
from messaging.templates import MissingValue, pick_bucket, render, variables_for

LINK = "https://pay.example/abc"


class TemplateTests(Base):
    def test_six_templates_are_seeded_with_msg91_ids(self):
        ids = dict(SmsTemplate.objects.values_list("key", "msg91_template_id"))
        self.assertEqual(ids, {
            "demand": "6a64b0274fec41f7f80f6403", "bkt0": "6ab622427f629adee5014d73", "bkt1": "6ab62394e384b81a5009d1e2",
            "bkt2": "6ab6233dd16941664d09be52", "bkt3": "6ab6205631857c9a860bea02", "npa": "6ab6227c642c9e8a59037302"})
        self.assertEqual(SmsTemplate.objects.get(key="bkt0").message_type, "UNICODE")
        self.assertEqual(SmsTemplate.objects.get(key="bkt3").message_type, "NORMAL")

    def test_bucket_is_picked_from_dpd(self):
        picks = {d: pick_bucket(d).key for d in (0, 1, 30, 31, 60, 61, 90, 91, 400)}
        self.assertEqual(picks, {0: "bkt0", 1: "bkt1", 30: "bkt1", 31: "bkt2", 60: "bkt2", 61: "bkt3", 90: "bkt3", 91: "npa", 400: "npa"})
        self.assertEqual(pick_bucket(-3).key, "bkt0")      # not yet due

    def test_a_switched_off_template_is_skipped(self):
        SmsTemplate.objects.filter(key="bkt2").update(active=False)
        self.assertIsNone(pick_bucket(45))

    def test_outstanding_is_the_days_closure_amount_in_whole_rupees(self):
        Loan = type(self.l1)
        Loan.objects.filter(pk=self.l2.pk).update(closure_amount=Decimal("12963"), dpd=12, installment_amount=Decimal("2500.00"))
        self.l2.refresh_from_db()
        t = SmsTemplate.objects.get(key="bkt1")
        v = variables_for(t, self.l2, LINK)
        self.assertEqual(v, {"number": "12", "number1": "12963", "url": LINK})
        self.assertEqual(render(t, v), f"Alpha Rupee: Your loan is overdue by 12 days. Outstanding ₹12963. Please pay immediately. {LINK} | +918065075438")

    def test_bkt0_uses_the_emi_amount(self):
        type(self.l1).objects.filter(pk=self.l1.pk).update(installment_amount=Decimal("2500.00"))
        self.l1.refresh_from_db()
        v = variables_for(SmsTemplate.objects.get(key="bkt0"), self.l1, LINK)
        self.assertEqual(v, {"number": "2500", "url": LINK})

    def test_no_payment_link_means_nothing_is_built(self):
        with self.assertRaises(MissingValue):
            variables_for(SmsTemplate.objects.get(key="npa"), self.l1, None)

    def test_demand_reminder_is_refused_until_the_due_date_field_exists(self):
        with self.assertRaises(MissingValue):
            variables_for(SmsTemplate.objects.get(key="demand"), self.l1, None)


class TemplateApiTests(Base):
    def test_head_can_list_agent_cannot(self):
        r = self.api(self.head).get("/api/messaging/templates")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([t["key"] for t in r.json()], ["demand", "bkt0", "bkt1", "bkt2", "bkt3", "npa"])
        self.assertEqual(self.api(self.a1).get("/api/messaging/templates").status_code, 403)

    def test_only_admin_can_change_ranges_and_audit_is_written(self):
        admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        self.assertEqual(self.api(self.head).put("/api/messaging/templates/bkt1", {"dpd_max": 20}, format="json").status_code, 403)
        r = self.api(admin).put("/api/messaging/templates/bkt1", {"dpd_max": 20, "msg91_template_id": "hacked", "body": "x"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        t = SmsTemplate.objects.get(key="bkt1")
        self.assertEqual((t.dpd_max, t.msg91_template_id), (20, "6ab62394e384b81a5009d1e2"))   # id and text cannot be edited
        self.assertTrue(AuditLog.objects.filter(action="sms_template_updated").exists())

    def test_bad_range_is_refused(self):
        admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        r = self.api(admin).put("/api/messaging/templates/bkt1", {"dpd_min": 40, "dpd_max": 10}, format="json")
        self.assertEqual(r.status_code, 400)
