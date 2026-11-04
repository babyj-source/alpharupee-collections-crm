from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, User
from loans.models import Loan, SyncRun
from loans.sync import run_sync


class FakeReader:
    def __init__(self, accounts, items, borrowers=()):
        self._a, self._i, self._b = accounts, items, borrowers

    def accounts(self):
        return iter(self._a)

    def items(self):
        return iter(self._i)

    def borrowers(self):
        return iter(self._b)


def acct(aid, status=0):
    return {"account_id": aid, "customer_id": f"C{aid}", "product_code": "PL", "branch_code": "B1", "operational_status": status}


def item(iid, aid, dpd, demand, fee=0, balance=-1000):
    return {"id": iid, "account_id": aid, "due_from_date": date(2026, 9, 1), "demand_due": demand, "fee_due": fee,
            "balance": balance, "installment_amount": 500, "dpd": dpd, "risk_dpd": dpd, "bom_dpd": dpd}


class SyncTests(TestCase):
    def test_creates_and_updates_and_keeps_crm_fields(self):
        r1 = FakeReader([acct("A1"), acct("A2", 2)], [item(1, "A1", 12, Decimal("500.50"), Decimal("10"))],
                        [{"account_id": "A1", "name": "Asha Rao", "mobile": "9876543210"}])
        run = run_sync(r1)
        self.assertTrue(run.ok)
        self.assertEqual((run.created, run.updated), (2, 0))
        loan = Loan.objects.get(account_id="A1")
        self.assertEqual(loan.overdue_amount, Decimal("510.50"))
        self.assertEqual(loan.borrower_mobile, "919876543210")
        self.assertEqual(loan.masked_mobile, "XXXXXXXX3210")
        loan.crm_status, loan.note = "promised", "calls on Friday"
        loan.save()
        # second run: a newer collection item for A1 wins; CRM fields survive
        r2 = FakeReader([acct("A1"), acct("A2", 2)],
                        [item(1, "A1", 12, 500), item(2, "A1", 20, 700)])
        run = run_sync(r2)
        self.assertEqual((run.created, run.updated), (0, 2))
        loan.refresh_from_db()
        self.assertEqual(loan.dpd, 20)
        self.assertEqual(loan.crm_status, "promised")
        self.assertEqual(loan.note, "calls on Friday")
        self.assertEqual(loan.borrower_name, "Asha Rao")  # not wiped when the borrower feed is empty

    def test_account_without_item_gets_zero_dues(self):
        run_sync(FakeReader([acct("A9")], []))
        self.assertEqual(Loan.objects.get().dpd, 0)

    def test_failure_is_recorded_not_raised(self):
        class Boom(FakeReader):
            def items(self):
                raise RuntimeError("lms down")

        run = run_sync(Boom([], []))
        self.assertFalse(run.ok)
        self.assertIn("lms down", run.error)
        self.assertEqual(SyncRun.objects.count(), 1)


class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.head = User.objects.create_user("h@x.com", "pw-not-checked-here", name="H", mobile="919000000001", role="head")
        self.a1 = User.objects.create_user("a1@x.com", "pw", name="Agent One", mobile="919000000002", role="agent")
        self.a2 = User.objects.create_user("a2@x.com", "pw", name="Agent Two", mobile="919000000003", role="agent")
        run_sync(FakeReader(
            [acct("L1"), acct("L2"), acct("L3"), acct("L4", 2)],
            [item(1, "L1", 5, 100), item(2, "L2", 45, 400, 20), item(3, "L3", 0, 0), item(4, "L4", 99, 900)],
            [{"account_id": "L1", "name": "Asha", "mobile": "9111111111"}, {"account_id": "L2", "name": "Ravi", "mobile": "9222222222"}],
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
        self.assertEqual(self.api(self.a1).post(f"/api/loans/{self.l2.pk}/mobile").status_code, 404)

    def test_head_sees_active_loans_worst_first(self):
        r = self.api(self.head).get("/api/loans").json()
        self.assertEqual([x["account_id"] for x in r["results"]], ["L2", "L1", "L3"])  # L4 is closed
        self.assertEqual(self.api(self.head).get("/api/loans?overdue=1&agent=none").json()["count"], 0)

    def test_api_never_returns_the_full_number(self):
        r = self.api(self.head).get(f"/api/loans/{self.l1.pk}").json()
        self.assertEqual(r["mobile"], "XXXXXXXX1111")
        self.assertNotIn("9111111111", str(r))

    def test_reveal_is_audited_and_scoped(self):
        r = self.api(self.a1).post(f"/api/loans/{self.l1.pk}/mobile")
        self.assertEqual(r.json()["mobile"], "919111111111")
        log = AuditLog.objects.get(action="mobile_revealed")
        self.assertEqual((log.user, log.object_id), (self.a1, str(self.l1.pk)))
        self.assertNotIn("9111111111", log.detail)

    def test_status_update(self):
        r = self.api(self.a1).patch(f"/api/loans/{self.l1.pk}", {"crm_status": "promised", "note": "Fri"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.l1.refresh_from_db()
        self.assertEqual((self.l1.crm_status, self.l1.note), ("promised", "Fri"))
        bad = self.api(self.a1).patch(f"/api/loans/{self.l1.pk}", {"crm_status": "bogus"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_assign_head_only(self):
        body = {"loan_ids": [self.l1.pk], "agent_id": self.a2.pk}
        self.assertEqual(self.api(self.a1).post("/api/loans/assign", body, format="json").status_code, 403)
        self.assertEqual(self.api(self.head).post("/api/loans/assign", body, format="json").json()["updated"], 1)
        self.l1.refresh_from_db()
        self.assertEqual(self.l1.assigned_agent, self.a2)
        wrong = {"loan_ids": [self.l1.pk], "agent_id": self.head.pk}  # a head is not an agent
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
