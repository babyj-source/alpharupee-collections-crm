from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, User
from loans.models import ACTIVE_Q, Loan, SyncRun
from loans.sync import run_sync


class FakeReader:
    """Same two methods as LmsReader, no database."""

    def __init__(self, accounts, customers=()):
        self._a, self._c = accounts, customers

    def accounts(self):
        return iter(self._a)

    def customers(self):
        return iter(self._c)


def acct(aid, dpd=0, demand=0, fee=0, status=0, holder=1, customer=None, first="Asha", last="Rao", udf=("", "", "")):
    return {
        "account_id": aid, "customer_id": customer or f"C{aid}", "holder_num": holder, "product_code": "PL",
        "branch_code": "B1", "operational_status": status, "dpd": dpd, "oldest_due_demand_date": date(2026, 9, 1),
        "last_repayment_date": date(2026, 8, 1), "total_demand_due_magnitude": demand, "total_fee_due_magnitude": fee,
        "balance_magnitude": -1000, "installment_amount_magnitude": 500,
        "first_name": first, "middle_name": None, "last_name": last,
        "udf_text2": udf[0], "udf_text3": udf[1], "udf_text4": udf[2],
    }


def cust(cid, phone1="9111111111", phone2="", ref="Meena Rao", ref_phone="9333333333", rel="Sister",
         ref2="", ref2_phone="", rel2=""):
    return {
        "customer_id": cid, "phone1": phone1, "phone2": phone2, "reference_name": ref, "reference_phone": ref_phone,
        "reference_relationship_type": rel, "reference_name2": ref2, "reference_phone2": ref2_phone,
        "reference_relationship_type2": rel2,
    }


class SyncTests(TestCase):
    def test_creates_updates_and_keeps_crm_fields(self):
        run = run_sync(FakeReader([acct("A1", 12, Decimal("500.50"), Decimal("10")), acct("A2", status=2)],
                                  [cust("CA1")]))
        self.assertTrue(run.ok)
        self.assertEqual((run.created, run.updated), (2, 0))
        loan = Loan.objects.get(account_id="A1")
        self.assertEqual(loan.overdue_amount, Decimal("510.50"))
        self.assertEqual(loan.dpd, 12)
        self.assertEqual(loan.borrower_name, "Asha Rao")
        self.assertEqual(loan.borrower_mobile, "919111111111")
        self.assertEqual((loan.ref1_name, loan.ref1_mobile, loan.ref1_relation), ("Meena Rao", "919333333333", "Sister"))
        loan.crm_status, loan.note = "promised", "calls on Friday"
        loan.save()
        run = run_sync(FakeReader([acct("A1", 20, 700), acct("A2", status=2)], [cust("CA1")]))
        self.assertEqual((run.created, run.updated), (0, 2))
        loan.refresh_from_db()
        self.assertEqual(loan.dpd, 20)
        self.assertEqual((loan.crm_status, loan.note), ("promised", "calls on Friday"))

    def test_reference_comes_from_the_account_udf_fields(self):
        run_sync(FakeReader(
            [acct("U1", udf=("Kamlesh Panchal", "9998637311", "Friend")),
             acct("U2", udf=("", "", "")),                                  # no UDF: falls back to the customer master
             acct("U3", udf=("Name Only", "not a number", "Friend")),       # unusable mobile: falls back as well
             acct("U4", status=2, udf=("Closed Person", "9998637312", "Friend"))],
            [cust("CU1", ref="Old Name", ref_phone="9333333333", ref2="Second", ref2_phone="9444444444", rel2="Cousin"),
             cust("CU2", ref="Meena Rao", ref_phone="9333333334", rel="Sister"),
             cust("CU3", ref="", ref_phone=""), cust("CU4")],
        ))
        u1 = Loan.objects.get(account_id="U1")
        self.assertEqual((u1.ref1_name, u1.ref1_mobile, u1.ref1_relation), ("Kamlesh Panchal", "919998637311", "Friend"))
        self.assertEqual((u1.ref2_name, u1.ref2_mobile), ("Second", "919444444444"))  # reference 2 still from the customer master
        u2 = Loan.objects.get(account_id="U2")
        self.assertEqual((u2.ref1_name, u2.ref1_mobile), ("Meena Rao", "919333333334"))
        u3 = Loan.objects.get(account_id="U3")
        self.assertEqual(u3.ref1_mobile, "")
        self.assertEqual(Loan.objects.get(account_id="U4").ref1_mobile, "")  # closed loans get no contact details

    def test_primary_holder_is_lowest_holder_num(self):
        run_sync(FakeReader([acct("J1", holder=2, first="Second", customer="CB"), acct("J1", holder=1, first="First", customer="CA")],
                            [cust("CA", phone1="9000000001"), cust("CB", phone1="9000000002")]))
        loan = Loan.objects.get(account_id="J1")
        self.assertEqual((loan.borrower_name, loan.borrower_mobile), ("First Rao", "919000000001"))
        self.assertEqual(Loan.objects.count(), 1)

    def test_contacts_only_for_active_loans(self):
        run_sync(FakeReader([acct("OPEN", status=0), acct("SHUT", status=2)], [cust("COPEN"), cust("CSHUT")]))
        self.assertTrue(Loan.objects.get(account_id="OPEN").borrower_mobile)
        shut = Loan.objects.get(account_id="SHUT")
        self.assertEqual((shut.borrower_mobile, shut.ref1_mobile), ("", ""))

    def test_missing_reference_and_missing_customer(self):
        run_sync(FakeReader([acct("N1"), acct("N2")], [cust("CN1", ref="", ref_phone="", rel="")]))
        n1, n2 = Loan.objects.get(account_id="N1"), Loan.objects.get(account_id="N2")
        self.assertEqual(n1.ref1_mobile, "")
        self.assertEqual([c["key"] for c in n1.contacts()], ["borrower"])
        self.assertEqual(n2.contacts(), [])

    def test_failure_is_recorded_not_raised(self):
        class Boom(FakeReader):
            def accounts(self):
                raise RuntimeError("lms down")

        run = run_sync(Boom([]))
        self.assertFalse(run.ok)
        self.assertIn("lms down", run.error)
        self.assertEqual(SyncRun.objects.count(), 1)

    def test_active_accepts_code_and_text(self):
        run_sync(FakeReader([acct("S1", status=0), acct("S2", status="Active"), acct("S3", status=2), acct("S4", status=1)]))
        ids = sorted(Loan.objects.filter(ACTIVE_Q).values_list("account_id", flat=True))
        self.assertEqual(ids, ["S1", "S2"])

    def test_reader_queries_avoid_sensitive_columns(self):
        from loans import lms

        text = (lms.ACCOUNT_COLUMNS + " " + lms.CUSTOMER_COLUMNS).lower()
        self.assertIn("udf_text3", text)  # reference mobile
        for banned in ("pan", "date_of_birth", "address", "email", "document", "encorepaydb"):
            self.assertNotIn(banned, text)


class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.head = User.objects.create_user("h@x.com", "pw-not-checked-here", name="H", mobile="919000000001", role="head")
        self.a1 = User.objects.create_user("a1@x.com", "pw", name="Agent One", mobile="919000000002", role="agent")
        self.a2 = User.objects.create_user("a2@x.com", "pw", name="Agent Two", mobile="919000000003", role="agent")
        run_sync(FakeReader(
            [acct("L1", 5, 100, first="Asha"), acct("L2", 45, 400, 20, first="Ravi"), acct("L3", 0, 0), acct("L4", 99, 900, status=2)],
            [cust("CL1", "9111111111", ref="Meena Rao", ref_phone="9333333333", rel="Sister"),
             cust("CL2", "9222222222", ref="", ref_phone=""),
             cust("CL3"), cust("CL4")],
        ))
        self.l1 = Loan.objects.get(account_id="L1")
        self.l2 = Loan.objects.get(account_id="L2")
        self.l1.assigned_agent = self.a1
        self.l1.save()
        self.l2.assigned_agent = self.a2
        self.l2.save()

    def api(self, user=None):
        c = APIClient()
        if user:
            c.force_login(user)
        return c

    def test_needs_login(self):
        self.assertEqual(self.api().get("/api/loans").status_code, 403)
        self.assertEqual(self.api().get("/api/dashboard").status_code, 403)

    def test_agent_sees_only_own_loans(self):
        r = self.api(self.a1).get("/api/loans").json()
        self.assertEqual([x["account_id"] for x in r["results"]], ["L1"])
        self.assertEqual(self.api(self.a1).get(f"/api/loans/{self.l2.pk}").status_code, 404)
        self.assertEqual(self.api(self.a1).patch(f"/api/loans/{self.l2.pk}", {"crm_status": "paid"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.a1).post(f"/api/loans/{self.l2.pk}/mobile", {"which": "borrower"}, format="json").status_code, 404)

    def test_head_sees_active_loans_worst_first(self):
        r = self.api(self.head).get("/api/loans").json()
        self.assertEqual([x["account_id"] for x in r["results"]], ["L2", "L1", "L3"])  # L4 is closed

    def test_api_never_returns_a_full_number(self):
        r = self.api(self.head).get(f"/api/loans/{self.l1.pk}")
        body = r.content.decode()
        for raw in ("9111111111", "9333333333"):
            self.assertNotIn(raw, body)
        data = r.json()
        self.assertEqual([c["key"] for c in data["contacts"]], ["borrower", "ref1"])
        self.assertEqual(data["contacts"][1]["name"], "Meena Rao")
        self.assertEqual(data["contacts"][1]["relation"], "Sister")
        self.assertEqual(data["contacts"][1]["mobile"], "XXXXXXXX3333")

    def test_reveal_names_the_person_in_the_audit_log(self):
        r = self.api(self.a1).post(f"/api/loans/{self.l1.pk}/mobile", {"which": "ref1"}, format="json")
        self.assertEqual(r.json(), {"which": "ref1", "mobile": "919333333333"})
        log = AuditLog.objects.get(action="mobile_revealed")
        self.assertEqual((log.user, log.object_id, log.detail), (self.a1, str(self.l1.pk), "ref1"))
        self.assertNotIn("9333333333", log.detail)
        default = self.api(self.a1).post(f"/api/loans/{self.l1.pk}/mobile", {}, format="json").json()
        self.assertEqual(default["which"], "borrower")
        self.assertEqual(self.api(self.a1).post(f"/api/loans/{self.l1.pk}/mobile", {"which": "ref9"}, format="json").status_code, 400)

    def test_reveal_of_missing_number_is_empty(self):
        r = self.api(self.a2).post(f"/api/loans/{self.l2.pk}/mobile", {"which": "ref1"}, format="json")
        self.assertEqual(r.json()["mobile"], "")

    def test_status_update_including_reference_contacted(self):
        r = self.api(self.a1).patch(f"/api/loans/{self.l1.pk}", {"crm_status": "reference_contacted", "note": "Sister said Fri"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.l1.refresh_from_db()
        self.assertEqual((self.l1.crm_status, self.l1.note), ("reference_contacted", "Sister said Fri"))
        bad = self.api(self.a1).patch(f"/api/loans/{self.l1.pk}", {"crm_status": "bogus"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_assign_head_only(self):
        body = {"loan_ids": [self.l1.pk], "agent_id": self.a2.pk}
        self.assertEqual(self.api(self.a1).post("/api/loans/assign", body, format="json").status_code, 403)
        self.assertEqual(self.api(self.head).post("/api/loans/assign", body, format="json").json()["updated"], 1)
        self.l1.refresh_from_db()
        self.assertEqual(self.l1.assigned_agent, self.a2)
        wrong = {"loan_ids": [self.l1.pk], "agent_id": self.head.pk}
        self.assertEqual(self.api(self.head).post("/api/loans/assign", wrong, format="json").status_code, 404)
        self.assertEqual(self.api(self.a1).get("/api/agents").status_code, 403)

    def test_dashboard_head_and_agent(self):
        h = self.api(self.head).get("/api/dashboard").json()
        self.assertEqual(h["accounts"], 3)
        self.assertEqual(h["overdue_accounts"], 2)
        self.assertEqual(Decimal(str(h["overdue_amount"])), Decimal("520"))
        by = {b["bucket"]: b["accounts"] for b in h["buckets"]}
        self.assertEqual(by, {"0": 1, "1-30": 1, "31-60": 1, "61-90": 0, "90+": 0})
        self.assertIn("by_agent", h)
        a = self.api(self.a1).get("/api/dashboard").json()
        self.assertEqual(a["accounts"], 1)
        self.assertNotIn("by_agent", a)
