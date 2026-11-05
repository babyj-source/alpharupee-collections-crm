from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from loans.models import Loan, Ptp
from loans.priority import score_loan
from loans.tests.test_workflow import Base
from loans.workflow import bucket_key


class PriorityTests(Base):
    def get(self, user, qs=""):
        return self.api(user).get(f"/api/priority{qs}")

    def test_bucket_keys(self):
        self.assertEqual([bucket_key(d) for d in (0, 1, 7, 8, 15, 16, 400)],
                         ["dpd0", "dpd1_7", "dpd1_7", "dpd8_15", "dpd8_15", "dpd16", "dpd16"])

    def test_head_sees_all_overdue_ranked_with_reasons(self):
        r = self.get(self.head).json()
        self.assertEqual(r["count"], 2)  # L3 owes nothing, L4 is closed
        ids = [x["account_id"] for x in r["results"]]
        self.assertEqual(ids, ["L2", "L1"])
        self.assertEqual((r["results"][0]["score"], r["results"][1]["score"]), (80, 40))
        self.assertEqual(r["results"][1]["reasons"], ["5 DPD", "Never called"])
        self.assertEqual(r["results"][0]["bucket"], "dpd16")

    def test_agent_only_sees_own(self):
        r = self.get(self.a1).json()
        self.assertEqual([x["account_id"] for x in r["results"]], ["L1"])
        # an agent cannot widen the view with ?agent=
        self.assertEqual(self.get(self.a1, f"?agent={self.a2.pk}").json()["count"], 1)

    def test_callback_due_promise_and_calls_move_the_score(self):
        now = timezone.now()
        Loan.objects.filter(pk=self.l1.pk).update(callback_at=now - timedelta(hours=1))
        top = self.get(self.head).json()["results"]
        self.assertEqual(top[0]["account_id"], "L1")                      # 40 + 50 beats 80
        self.assertIn("Call-back due", top[0]["reasons"])
        Ptp.objects.create(loan=self.l1, agent=self.a1, promised_date=self.day(3), amount=100, reason="forgot")
        one = lambda: [x for x in self.get(self.head).json()["results"] if x["account_id"] == "L1"][0]  # noqa: E731
        self.assertEqual(one()["score"], 40 + 50 - 30)
        self.assertIn("Promise given", one()["reasons"])
        Ptp.objects.filter(loan=self.l1).update(promised_date=self.day(-1))
        self.assertEqual(one()["score"], 40 + 50 + 45)
        self.assertIn("Promise missed", one()["reasons"])
        Loan.objects.filter(pk=self.l2.pk).update(last_disposition_at=now)
        l2 = [x for x in self.get(self.head).json()["results"] if x["account_id"] == "L2"][0]
        self.assertEqual((l2["score"], l2["reasons"][-1]), (65 - 10, "Called today"))

    def test_score_of_an_old_call(self):
        loan = Loan.objects.get(pk=self.l1.pk)
        loan.last_disposition_at = timezone.now() - timedelta(days=5)
        pts, reasons = score_loan(loan, timezone.now(), timezone.localdate())
        self.assertEqual(pts, 25 + 10)
        self.assertEqual(reasons[-1], "Not called for 5 days")

    def test_filters_and_bad_input(self):
        self.assertEqual([x["account_id"] for x in self.get(self.head, "?bucket=dpd1_7").json()["results"]], ["L1"])
        self.assertEqual(self.get(self.head, f"?agent={self.a2.pk}").json()["count"], 1)
        self.assertEqual(self.get(self.head, "?agent=none").json()["count"], 0)
        self.assertEqual(len(self.get(self.head, "?limit=1").json()["results"]), 1)
        self.assertEqual(self.get(self.head, "?limit=abc").status_code, 400)
        self.assertEqual(self.get(self.head, "?agent=abc").status_code, 400)

    def test_numbers_stay_masked(self):
        text = self.get(self.head).content.decode()
        self.assertNotIn("9111111111", text)


class MisTests(Base):
    def setUp(self):
        super().setUp()
        self.api(self.a1).post(f"/api/loans/{self.l1.pk}/disposition", {"code": "no_answer"}, format="json")
        self.api(self.a1).post(f"/api/loans/{self.l1.pk}/disposition",
                               {"code": "ptp_given", "ptp_date": str(self.day(3)), "ptp_reason": "forgot"}, format="json")
        self.api(self.a2).post(f"/api/loans/{self.l2.pk}/disposition", {"code": "refuses"}, format="json")

    def mis(self, user=None, qs=""):
        return self.api(user or self.head).get(f"/api/mis{qs}")

    def test_head_and_admin_only(self):
        self.assertEqual(self.mis(self.a1).status_code, 403)
        admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        self.assertEqual(self.mis(admin).status_code, 200)

    def test_portfolio_by_bucket(self):
        b = {x["bucket"]: x for x in self.mis().json()["portfolio"]}
        self.assertEqual([b[k]["accounts"] for k in ("dpd0", "dpd1_7", "dpd8_15", "dpd16")], [1, 1, 0, 1])  # L4 is closed
        self.assertEqual(Decimal(b["dpd16"]["overdue_amount"]), Decimal("420"))
        self.assertEqual(Decimal(b["dpd16"]["closure_amount"]), Decimal("1020"))
        self.assertEqual(Decimal(b["dpd1_7"]["outstanding_principal"]), Decimal("1000"))
        self.assertEqual(b["dpd1_7"]["unassigned"], 0)
        self.assertEqual(b["dpd0"]["unassigned"], 1)

    def test_calls_outcomes_and_agents(self):
        d = self.mis().json()
        self.assertEqual((d["calls"]["total"], d["calls"]["connected"], d["calls"]["contact_rate"]), (3, 2, 66.7))
        self.assertEqual(d["calls"]["accounts_worked"], 2)
        out = {o["code"]: o["count"] for o in d["outcomes"]}
        self.assertEqual((out["no_answer"], out["ptp_given"], out["refuses"], out["wrong_number"]), (1, 1, 1, 0))
        rows = {a["agent"]: a for a in d["agents"]}
        a1 = rows["Agent One"]
        self.assertEqual((a1["calls"], a1["connected"], a1["accounts_worked"], a1["promises"], a1["assigned_accounts"]), (2, 1, 1, 1, 1))
        self.assertEqual(Decimal(a1["promise_amount"]), Decimal("100"))
        self.assertEqual(rows["Agent Two"]["promises"], 0)
        self.assertEqual(d["daily"][-1]["calls"], 3)

    def test_promise_performance(self):
        Ptp.objects.create(loan=self.l2, agent=self.a2, promised_date=self.day(-2), amount=400, reason="forgot",
                           status="kept", closed_at=timezone.now())
        Ptp.objects.create(loan=self.l1, agent=self.a1, promised_date=self.day(-1), amount=50, reason="forgot", status="open")
        Ptp.objects.create(loan=self.l1, agent=self.a1, promised_date=self.day(-3), amount=50, reason="forgot", status="superseded")
        p = self.mis().json()["promises"]
        self.assertEqual((p["due"], p["kept"], p["broken"], p["missed_open"], p["kept_rate"]), (2, 1, 0, 1, 50.0))
        self.assertEqual(Decimal(p["kept_amount"]), Decimal("400"))
        rows = {a["agent"]: a for a in self.mis().json()["agents"]}
        self.assertEqual(rows["Agent Two"]["promises_kept"], 1)

    def test_period_parameter(self):
        self.assertEqual(self.mis(qs="?days=7").json()["period"]["days"], 7)
        self.assertEqual(self.mis(qs="?days=1").json()["period"]["days"], 1)
        self.assertEqual(self.mis(qs="?days=abc").json()["period"]["days"], 30)
        self.assertEqual(len(self.mis(qs="?days=7").json()["daily"]), 7)
