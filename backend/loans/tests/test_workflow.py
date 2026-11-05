from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, User
from loans.csvio import safe_cell
from loans.models import Activity, AllocationBatch, Loan, Ptp, ReferenceContact
from loans.phones import normalise_mobile
from loans.sync import run_sync

from .test_loans import FakeReader, acct, cust


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.head = User.objects.create_user("h@x.com", "pw", name="Head", mobile="919000000001", role="head")
        self.a1 = User.objects.create_user("a1@x.com", "pw", name="Agent One", mobile="919000000002", role="agent")
        self.a2 = User.objects.create_user("a2@x.com", "pw", name="Agent Two", mobile="919000000003", role="agent")
        run_sync(FakeReader(
            [acct("L1", 5, 100, first="Asha"), acct("L2", 45, 400, 20, first="Ravi"), acct("L3", 0, 0), acct("L4", 99, 900, status=2)],
            [cust("CL1", "9111111111", ref="Meena Rao", ref_phone="9333333333", rel="Sister"),
             cust("CL2", "9222222222", ref="", ref_phone=""), cust("CL3"), cust("CL4")],
        ))
        self.l1, self.l2 = Loan.objects.get(account_id="L1"), Loan.objects.get(account_id="L2")
        self.l3, self.l4 = Loan.objects.get(account_id="L3"), Loan.objects.get(account_id="L4")
        Loan.objects.filter(pk=self.l1.pk).update(assigned_agent=self.a1)
        Loan.objects.filter(pk=self.l2.pk).update(assigned_agent=self.a2)

    def api(self, user=None):
        c = APIClient()
        if user:
            c.force_login(user)
        return c

    @staticmethod
    def day(n=0):
        return timezone.localdate() + timedelta(days=n)


class DispositionTests(Base):
    def post(self, user, loan, **body):
        return self.api(user).post(f"/api/loans/{loan.pk}/disposition", body, format="json")

    def test_ptp_creates_promise_sets_status_and_timeline(self):
        r = self.post(self.a1, self.l1, code="ptp_given", ptp_date=str(self.day(3)), ptp_reason="salary_delayed", note="will pay Friday")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d["crm_status"], "promised")
        self.assertEqual(d["last_disposition"], "ptp_given")
        self.assertEqual(Decimal(d["open_ptp"]["amount"]), Decimal("100"))  # defaults to the overdue amount
        self.assertEqual(Ptp.objects.filter(loan=self.l1, status="open").count(), 1)
        tl = self.api(self.a1).get(f"/api/loans/{self.l1.pk}/timeline").json()
        self.assertEqual(tl[0]["disposition_label"], "Connected · PTP given")
        self.assertIn("will pay Friday", tl[0]["detail"])
        self.assertEqual(tl[0]["by"], "Agent One")

    def test_new_promise_replaces_the_old_one(self):
        for n in (2, 4):
            self.post(self.a1, self.l1, code="ptp_given", ptp_date=str(self.day(n)), ptp_reason="forgot", ptp_amount="50")
        self.assertEqual(Ptp.objects.filter(loan=self.l1, status="open").count(), 1)
        self.assertEqual(Ptp.objects.filter(loan=self.l1, status="superseded").count(), 1)

    def test_promise_rules(self):
        base = dict(code="ptp_given", ptp_reason="forgot")
        self.assertEqual(self.post(self.a1, self.l1, **base, ptp_date=str(self.day(8))).status_code, 400)  # too far
        self.assertEqual(self.post(self.a1, self.l1, **base, ptp_date=str(self.day(-1))).status_code, 400)  # past
        self.assertEqual(self.post(self.a1, self.l1, **base).status_code, 400)  # no date
        self.assertEqual(self.post(self.a1, self.l1, code="ptp_given", ptp_date=str(self.day(1))).status_code, 400)  # no reason
        self.assertEqual(self.post(self.a1, self.l1, **base, ptp_date=str(self.day(7))).status_code, 200)  # the limit
        self.assertEqual(self.post(self.a1, self.l3, **base, ptp_date=str(self.day(1))).status_code, 404)  # not theirs
        self.assertEqual(Ptp.objects.count(), 1)

    def test_no_promise_when_nothing_is_overdue(self):
        Loan.objects.filter(pk=self.l1.pk).update(demand_due=0, fee_due=0)
        r = self.post(self.a1, self.l1, code="ptp_given", ptp_date=str(self.day(1)), ptp_reason="forgot")
        self.assertEqual(r.status_code, 400)

    def test_paid_closes_the_open_promise(self):
        self.post(self.a1, self.l1, code="ptp_given", ptp_date=str(self.day(2)), ptp_reason="forgot")
        r = self.post(self.a1, self.l1, code="paid_today")
        self.assertEqual(r.json()["crm_status"], "paid")
        self.assertEqual(Ptp.objects.get(loan=self.l1).status, "kept")

    def test_other_outcomes_map_to_statuses(self):
        for code, status in [("no_answer", "not_reachable"), ("dispute", "dispute"), ("reference_contacted", "reference_contacted"),
                             ("refuses", "contacted")]:
            self.assertEqual(self.post(self.a1, self.l1, code=code).json()["crm_status"], status)
        self.assertEqual(self.post(self.a1, self.l1, code="nonsense").status_code, 400)
        self.assertEqual(self.post(self.a1, self.l1, code="call_back_later", callback_date=str(self.day(1)), callback_time="11:00").json()["crm_status"], "contacted")

    def test_closed_account_is_refused_and_audit_has_no_note_text(self):
        Loan.objects.filter(pk=self.l4.pk).update(assigned_agent=self.a1)
        self.assertEqual(self.post(self.a1, self.l4, code="no_answer").status_code, 400)
        self.post(self.a1, self.l1, code="refuses", note="secret detail")
        blob = " ".join(f"{a.action} {a.detail}" for a in AuditLog.objects.all())
        self.assertIn("disposition_saved refuses", blob)
        self.assertNotIn("secret detail", blob)

    def test_timeline_is_scoped(self):
        self.assertEqual(self.api(self.a1).get(f"/api/loans/{self.l2.pk}/timeline").status_code, 404)
        self.assertEqual(self.api(self.head).get(f"/api/loans/{self.l2.pk}/timeline").status_code, 200)


class MetaTests(Base):
    def test_meta_lists_rules(self):
        self.assertEqual(self.api().get("/api/meta").status_code, 403)
        d = self.api(self.a1).get("/api/meta").json()
        self.assertEqual(d["ptp_max_days"], 7)
        self.assertEqual(d["today"], str(self.day(0)))
        self.assertIn("ptp_given", [x["code"] for x in d["dispositions"]])
        self.assertIn("salary_delayed", [x["code"] for x in d["ptp_reasons"]])


class CallbackTests(Base):
    def post(self, user, loan, **body):
        return self.api(user).post(f"/api/loans/{loan.pk}/disposition", {"code": "call_back_later", **body}, format="json")

    def future_slot(self):
        """A valid slot: tomorrow at 11:00 (always inside the calling window and in the future)."""
        return {"callback_date": str(self.day(1)), "callback_time": "11:00"}

    def test_needs_date_and_time(self):
        self.assertEqual(self.post(self.a1, self.l1).status_code, 400)
        self.assertEqual(self.post(self.a1, self.l1, callback_date=str(self.day(1))).status_code, 400)
        self.assertEqual(self.post(self.a1, self.l1, callback_time="11:00").status_code, 400)
        self.assertIsNone(Loan.objects.get(pk=self.l1.pk).callback_at)

    def test_saved_with_date_and_time_and_shown_in_timeline(self):
        r = self.post(self.a1, self.l1, **self.future_slot(), note="at office now")
        self.assertEqual(r.status_code, 200, r.content)
        loan = Loan.objects.get(pk=self.l1.pk)
        local = timezone.localtime(loan.callback_at)
        self.assertEqual((local.date(), local.strftime("%H:%M")), (self.day(1), "11:00"))
        self.assertEqual(r.json()["last_disposition"], "call_back_later")
        tl = self.api(self.a1).get(f"/api/loans/{self.l1.pk}/timeline").json()
        self.assertIn("at 11:00", tl[0]["detail"])
        self.assertIn("at office now", tl[0]["detail"])

    def test_rules(self):
        self.assertEqual(self.post(self.a1, self.l1, callback_date=str(self.day(1)), callback_time="07:30").status_code, 400)  # too early
        self.assertEqual(self.post(self.a1, self.l1, callback_date=str(self.day(1)), callback_time="19:00").status_code, 400)  # window closed
        self.assertEqual(self.post(self.a1, self.l1, callback_date=str(self.day(-1)), callback_time="11:00").status_code, 400)  # past
        self.assertEqual(self.post(self.a1, self.l1, callback_date=str(self.day(8)), callback_time="11:00").status_code, 400)  # too far
        self.assertEqual(self.post(self.a1, self.l1, callback_date=str(self.day(1)), callback_time="9am").status_code, 400)
        self.assertEqual(self.post(self.a1, self.l1, callback_date=str(self.day(7)), callback_time="08:00").status_code, 200)
        self.assertEqual(self.post(self.a1, self.l3, **self.future_slot()).status_code, 404)  # not theirs

    def test_later_outcomes_keep_or_clear_it(self):
        self.post(self.a1, self.l1, **self.future_slot())
        for code in ("no_answer", "not_reachable"):  # borrower still not reached: call-back stays
            self.api(self.a1).post(f"/api/loans/{self.l1.pk}/disposition", {"code": code}, format="json")
            self.assertIsNotNone(Loan.objects.get(pk=self.l1.pk).callback_at)
        self.api(self.a1).post(f"/api/loans/{self.l1.pk}/disposition", {"code": "refuses"}, format="json")
        self.assertIsNone(Loan.objects.get(pk=self.l1.pk).callback_at)  # reached: handled

    def test_list_scope_states_and_clear(self):
        slot = self.future_slot()
        self.post(self.a1, self.l1, **slot)
        self.post(self.head, self.l2, **slot)
        Loan.objects.filter(pk=self.l2.pk).update(callback_at=timezone.now() - timedelta(minutes=5))  # now overdue
        mine = self.api(self.a1).get("/api/callbacks").json()["results"]
        self.assertEqual([c["account_id"] for c in mine], ["L1"])
        self.assertEqual(mine[0]["state"], "upcoming")
        allc = self.api(self.head).get("/api/callbacks").json()["results"]
        self.assertEqual([(c["account_id"], c["state"]) for c in allc], [("L2", "due"), ("L1", "upcoming")])
        self.assertEqual(len(self.api(self.head).get("/api/callbacks?state=due").json()["results"]), 1)
        self.assertEqual(self.api(self.head).get("/api/callbacks?state=bogus").status_code, 400)
        d = self.api(self.head).get("/api/dashboard").json()
        self.assertEqual(d["callbacks"]["due"], 1)
        # clear
        self.assertEqual(self.api(self.a1).delete(f"/api/loans/{self.l2.pk}/callback").status_code, 404)
        r = self.api(self.a1).delete(f"/api/loans/{self.l1.pk}/callback")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["callback_at"])
        self.assertEqual(self.api(self.a1).delete(f"/api/loans/{self.l1.pk}/callback").status_code, 400)

    def test_meta_has_the_window(self):
        d = self.api(self.a1).get("/api/meta").json()
        self.assertEqual((d["call_start"], d["call_end"], d["callback_step_minutes"]), ("08:00", "19:00", 30))
        self.assertRegex(d["now"], r"^\d\d:\d\d$")


class PtpTests(Base):
    def setUp(self):
        super().setUp()
        self.p1 = Ptp.objects.create(loan=self.l1, agent=self.a1, promised_date=self.day(0), amount=100, reason="forgot")
        self.p2 = Ptp.objects.create(loan=self.l2, agent=self.a2, promised_date=self.day(-2), amount=420, reason="forgot")

    def test_lists_are_scoped_and_states_computed(self):
        mine = self.api(self.a1).get("/api/ptps").json()["results"]
        self.assertEqual([p["account_id"] for p in mine], ["L1"])
        self.assertEqual(mine[0]["state"], "today")
        allp = self.api(self.head).get("/api/ptps").json()["results"]
        self.assertEqual({p["account_id"]: p["state"] for p in allp}, {"L1": "today", "L2": "lapsed"})
        self.assertEqual(len(self.api(self.head).get("/api/ptps?state=lapsed").json()["results"]), 1)
        self.assertEqual(self.api(self.head).get("/api/ptps?state=bogus").status_code, 400)

    def test_close_promise_and_scope(self):
        self.assertEqual(self.api(self.a1).patch(f"/api/ptps/{self.p2.pk}", {"status": "broken"}, format="json").status_code, 404)
        r = self.api(self.a1).patch(f"/api/ptps/{self.p1.pk}", {"status": "broken"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Ptp.objects.get(pk=self.p1.pk).status, "broken")
        self.assertEqual(self.api(self.a1).patch(f"/api/ptps/{self.p1.pk}", {"status": "kept"}, format="json").status_code, 400)

    def test_dashboard_counts(self):
        d = self.api(self.head).get("/api/dashboard").json()
        self.assertEqual((d["ptp"]["open"]["count"], d["ptp"]["today"]["count"], d["ptp"]["lapsed"]["count"]), (2, 1, 1))
        d = self.api(self.a1).get("/api/dashboard").json()
        self.assertEqual(d["ptp"]["open"]["count"], 1)

    def test_sync_keeps_promise_when_nothing_overdue(self):
        run_sync(FakeReader([acct("L1", 0, 0), acct("L2", 45, 400, 20), acct("L3"), acct("L4", status=2)],
                            [cust("CL1"), cust("CL2"), cust("CL3"), cust("CL4")]))
        self.assertEqual(Ptp.objects.get(pk=self.p1.pk).status, "kept")
        self.assertEqual(Ptp.objects.get(pk=self.p2.pk).status, "open")


class ReferenceTests(Base):
    def put(self, user, loan, slot, **body):
        return self.api(user).put(f"/api/loans/{loan.pk}/references/{slot}", body, format="json")

    def test_agent_adds_reference_and_it_survives_a_sync(self):
        r = self.put(self.a1, self.l1, 2, name="Raju Kumar", relation="Friend", mobile="+91 98765 43210")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn("9876543210", r.content.decode())
        slots = r.json()["ref_slots"]
        self.assertEqual((slots[1]["name"], slots[1]["mobile"]), ("Raju Kumar", "XXXXXXXX3210"))
        run_sync(FakeReader([acct("L1", 5, 100), acct("L2", 45, 400, 20), acct("L3"), acct("L4", status=2)],
                            [cust("CL1", ref2="Someone Else", ref2_phone="9444444444"), cust("CL2"), cust("CL3"), cust("CL4")]))
        self.assertEqual(Loan.objects.get(pk=self.l1.pk).number_for("ref2"), "919876543210")  # CRM value wins over the LMS copy
        full = self.api(self.a1).post(f"/api/loans/{self.l1.pk}/mobile", {"which": "ref2"}, format="json").json()
        self.assertEqual(full["mobile"], "919876543210")

    def test_edit_name_without_retyping_number(self):
        r = self.put(self.a1, self.l1, 1, name="Meena R", relation="Sister")  # slot 1 came from the LMS
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ReferenceContact.objects.get(loan=self.l1, slot=1).mobile, "919333333333")

    def test_validation(self):
        self.assertEqual(self.put(self.a1, self.l1, 2, name="X").status_code, 400)  # no number at all
        self.assertEqual(self.put(self.a1, self.l1, 2, name="X", mobile="12345").status_code, 400)
        self.assertEqual(self.put(self.a1, self.l1, 2, name="X", mobile="9111111111").status_code, 400)  # the borrower's own number
        self.assertEqual(self.put(self.a1, self.l1, 3, name="X", mobile="9876543210").status_code, 400)
        self.assertEqual(self.put(self.a1, self.l2, 2, name="X", mobile="9876543210").status_code, 404)  # not theirs
        self.assertEqual(self.put(self.head, self.l4, 2, name="X", mobile="9876543210").status_code, 400)  # closed

    def test_remove_hides_the_lms_value(self):
        r = self.api(self.a1).delete(f"/api/loans/{self.l1.pk}/references/1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["key"] for c in r.json()["contacts"]], ["borrower"])

    def test_audit_has_no_numbers(self):
        self.put(self.a1, self.l1, 2, name="Raju", mobile="9876543210")
        blob = " ".join(f"{a.action} {a.detail}" for a in AuditLog.objects.all()) + " ".join(a.detail for a in Activity.objects.all())
        self.assertNotIn("9876543210", blob)
        self.assertIn("reference_saved", blob)

    def test_csv_upload_preview_then_apply(self):
        text = ("account_id,slot,ref_name,ref_mobile,ref_relation\n"
                "L1,2,Raju Kumar,9876543210,Friend\n"
                "L2,1,Sita Devi,98765 43211,Mother\n"
                "L2,1,Duplicate,9876543212,\n"
                "L1,1,Same as borrower,9111111111,\n"
                "NOPE,1,Ghost,9876543213,\n"
                "L3,1,Bad number,123,\n"
                "L4,1,Closed loan,9876543214,\n"
                "L3,2,=HYPERLINK(\"x\"),9876543215,\n")
        r = self.api(self.head).post("/api/references/csv", {"text": text, "apply": False}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["counts"], {"rows": 8, "ok": 3, "errors": 5})
        self.assertNotIn("9876543210", r.content.decode())  # preview shows masked numbers only
        self.assertEqual(ReferenceContact.objects.count(), 0)  # a preview changes nothing
        r = self.api(self.head).post("/api/references/csv", {"text": text, "apply": True}, format="json")
        self.assertEqual(ReferenceContact.objects.count(), 3)
        self.assertEqual(ReferenceContact.objects.get(loan=self.l3, slot=2).name, 'HYPERLINK("x")')  # formula start stripped
        self.assertTrue(AuditLog.objects.filter(action="references_uploaded").exists())

    def test_csv_upload_head_only_and_bad_header(self):
        self.assertEqual(self.api(self.a1).post("/api/references/csv", {"text": "a,b\n1,2"}, format="json").status_code, 403)
        r = self.api(self.head).post("/api/references/csv", {"text": "foo,bar\n1,2"}, format="json")
        self.assertEqual(r.status_code, 400)


class AllocationTests(Base):
    def post(self, text, apply=False, user=None):
        return self.api(user or self.head).post("/api/allocation/csv", {"text": text, "apply": apply, "filename": "f.csv"}, format="json")

    TEXT = ("loan_id,agent\n"
            "L1,a2@x.com\n"          # move a1 -> a2
            "L2,Agent Two\n"         # same
            "L3,Agent One\n"         # unassigned -> a1 (no dues, still active)
            "L1,a1@x.com\n"          # duplicate
            "L4,a1@x.com\n"          # closed
            "NOPE,a1@x.com\n"
            "L2,\n"                  # duplicate and empty agent: duplicate wins
            "L3x,nobody@x.com\n")

    def test_preview_changes_nothing_and_shows_load(self):
        r = self.post(self.TEXT)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["counts"], {"rows": 8, "move": 2, "same": 1, "errors": 5})
        load = {x["agent"]: (x["before"], x["after"]) for x in body["load"]}
        self.assertEqual(load["Agent One"], (1, 0))  # L1 leaves; L3 arrives but has no dues, so it is not counted as load
        self.assertEqual(load["Agent Two"], (1, 2))
        self.assertEqual(Loan.objects.get(pk=self.l1.pk).assigned_agent_id, self.a1.pk)
        self.assertEqual(AllocationBatch.objects.count(), 0)

    def test_apply_moves_loans_and_records_history(self):
        r = self.post(self.TEXT, apply=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Loan.objects.get(pk=self.l1.pk).assigned_agent_id, self.a2.pk)
        self.assertEqual(Loan.objects.get(pk=self.l3.pk).assigned_agent_id, self.a1.pk)
        self.assertIsNone(Loan.objects.get(pk=self.l4.pk).assigned_agent_id)
        self.assertEqual(Activity.objects.filter(kind="allocation").count(), 2)
        h = self.api(self.head).get("/api/allocation/history").json()
        self.assertEqual((h[0]["source"], h[0]["moved"], h[0]["errors"], h[0]["by"]), ("csv", 2, 5, "Head"))
        self.assertTrue(AuditLog.objects.filter(action="loans_allocated").exists())

    def test_unassign_keyword(self):
        r = self.post("loan_id,agent\nL1,unassigned\n", apply=True)
        self.assertEqual(r.json()["counts"]["move"], 1)
        self.assertIsNone(Loan.objects.get(pk=self.l1.pk).assigned_agent_id)

    def test_head_only_and_bad_files(self):
        self.assertEqual(self.post("loan_id,agent\nL1,a2@x.com", user=self.a1).status_code, 403)
        self.assertEqual(self.api(self.a1).get("/api/allocation/history").status_code, 403)
        self.assertEqual(self.post("x,y\n1,2").status_code, 400)
        self.assertEqual(self.post("   ").status_code, 400)

    def test_tick_box_assignment_is_logged_too(self):
        r = self.api(self.head).post("/api/loans/assign", {"loan_ids": [self.l1.pk, self.l3.pk], "agent_id": self.a1.pk}, format="json")
        self.assertEqual(r.json()["updated"], 1)  # l1 was already with a1
        b = AllocationBatch.objects.get()
        self.assertEqual((b.source, b.rows, b.moved, b.same), ("tick", 2, 1, 1))

    def test_template_download(self):
        r = self.api(self.head).get("/api/allocation/template.csv")
        self.assertEqual(r.status_code, 200)
        text = r.content.decode("utf-8-sig")
        self.assertIn("loan_id,agent,borrower,current_agent,dpd,overdue_inr", text)
        self.assertIn("L2,a2@x.com,Ravi Rao,Agent Two,45,420.00", text)
        self.assertNotIn("L3,", text)  # no dues
        self.assertEqual(self.api(self.a1).get("/api/allocation/template.csv").status_code, 403)


class ExportTests(Base):
    def test_head_only_masked_and_logged(self):
        self.assertEqual(self.api(self.a1).get("/api/loans/export.csv").status_code, 403)
        r = self.api(self.head).get("/api/loans/export.csv?overdue=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        text = r.content.decode("utf-8-sig")
        self.assertIn("XXXXXXXX1111", text)
        for raw in ("9111111111", "9333333333"):
            self.assertNotIn(raw, text)
        self.assertEqual(len(text.strip().splitlines()), 3)  # header + L2 + L1 (L3 has no overdue, L4 is closed)
        self.assertIn("Agent Two", text)
        self.assertTrue(AuditLog.objects.filter(action="loans_exported", detail="2 rows").exists())

    def test_formula_text_is_neutralised(self):
        Loan.objects.filter(pk=self.l1.pk).update(borrower_name="=cmd|' /C calc'!A0")
        text = self.api(self.head).get("/api/loans/export.csv").content.decode("utf-8-sig")
        self.assertIn("'=cmd", text)
        self.assertEqual(safe_cell("@x"), "'@x")
        self.assertEqual(safe_cell("Asha"), "Asha")

    def test_bad_filter_value_is_a_400_not_a_crash(self):
        self.assertEqual(self.api(self.head).get("/api/loans?dpd_min=abc").status_code, 400)


class PhoneTests(TestCase):
    def test_normalise(self):
        self.assertEqual(normalise_mobile("98765 43210"), "919876543210")
        self.assertEqual(normalise_mobile("+91-9876543210"), "919876543210")
        self.assertEqual(normalise_mobile("09876543210"), "919876543210")
        for bad in ("", "12345", "5876543210", "98765432101234", None, "abcdefghij"):
            self.assertEqual(normalise_mobile(bad), "")
