from datetime import timedelta

from django.utils import timezone

from loans.models import Activity
from dialer.models import DialAttempt, DispositionMap, PushedLead
from dialer.smartflo import SmartfloError
from dialer.tests.test_dialer import DialerBase


class QueueTests(DialerBase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        DispositionMap.objects.filter(crm_code="no_answer").update(smartflo_status_id="555")
        self.start()

    def poll(self):
        return self.api(self.a1).get("/api/dialer/session").json()

    def talked(self, seconds):
        """Pretend the live call has been up `seconds` already (the tests poll back to back)."""
        now = timezone.now()
        DialAttempt.objects.update(connected_at=now - timedelta(seconds=seconds + 1), last_seen_at=now - timedelta(seconds=1), started_at=now - timedelta(seconds=seconds + 5))

    def answered(self, number="9111111111", cid="c-1"):
        self.fake.live = {"data": [{"call_id": cid, "customer_number": number, "status": "answered", "call_time": "00:00:12"}]}

    def test_queue_lists_the_customers_sent_today_with_a_waiting_status(self):
        d = self.poll()
        self.assertEqual([(r["account_id"], r["status"]) for r in d["queue"]], [("L1", "waiting")])
        self.assertEqual(d["stats"], {"total": 1, "waiting": 1, "calling": 0, "done": 0, "no_answer": 0, "pending": 0})
        self.assertNotIn("9111111111", str(d))

    def test_a_ringing_then_connected_then_ended_call_moves_through_the_statuses(self):
        self.live()
        d = self.poll()
        self.assertEqual((d["queue"][0]["status"], d["call"]["phase"], d["stats"]["calling"]), ("calling", "ringing", 1))
        self.answered()
        d = self.poll()
        self.assertEqual((d["queue"][0]["status"], d["call"]["phase"], d["log"][0]["result"]), ("on_call", "connected", "On a call"))
        self.talked(60)
        self.fake.live = {"data": []}
        d = self.poll()
        self.assertEqual((d["queue"][0]["status"], d["awaiting_outcome"], d["log"][0]["result"][:5]), ("outcome_pending", True, "Spoke"))
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "no_answer"})
        self.assertEqual(r.status_code, 200, r.content)
        d = self.poll()
        self.assertEqual((d["queue"][0]["status"], d["queue"][0]["status_text"], d["awaiting_outcome"]), ("done", "No answer", False))

    def test_a_call_nobody_answered_needs_no_outcome_and_the_next_one_shows(self):
        self.live()
        self.poll()
        self.fake.live = {"data": []}
        d = self.poll()
        self.assertEqual((d["queue"][0]["status"], d["queue"][0]["status_text"], d["awaiting_outcome"], d["log"][0]["result"], d["queue"][0]["attempts"]),
                         ("done", "No answer (automatic)", False, "Not answered", 1))
        a = Activity.objects.get(loan=self.l1, kind="disposition")
        self.assertEqual((a.disposition, a.detail, a.user_id), ("no_answer", "Automatic: the customer did not answer", self.a1.pk))
        self.l1.refresh_from_db()
        self.assertEqual((self.l1.last_disposition, self.l1.crm_status), ("no_answer", "new"))        # the status and promises are left alone
        self.assertEqual([c for c in self.fake.calls if c[0] == "disposition"], [])                     # Smartflo needs nothing for a call nobody answered
        self.fake.live = {"data": [{"call_id": "c-2", "customer_number": "9111111111", "status": "ringing"}]}
        d = self.poll()
        self.assertEqual((d["queue"][0]["status"], d["queue"][0]["attempts"], len(d["log"])), ("calling", 2, 2))

    def test_when_smartflos_agent_filter_shows_nothing_the_call_is_found_by_the_agents_own_customers(self):
        original = self.fake.live_calls

        def live_calls(token, agent_number=None):
            self.fake.calls.append(("live_calls", agent_number))
            if agent_number:
                return {"data": []}
            return {"data": [{"call_id": "c-9", "customer_number": "9111111111", "status": "answered"},
                             {"call_id": "c-8", "customer_number": "9222222222", "status": "answered"}]}   # another agent's customer
        self.fake.live_calls = live_calls
        d = self.poll()
        self.assertEqual((d["live"], d["call"]["phase"], d["current"]["account_id"]), ({"seen": 1, "via": "account"}, "connected", "L1"))
        self.assertEqual(DialAttempt.objects.get().call_id, "c-9")                 # the other agent's call is not ours
        self.fake.live_calls = original

    def test_nothing_live_says_so_in_the_diagnostics(self):
        d = self.poll()
        self.assertEqual(d["live"], {"seen": 0, "via": "none"})

    def test_a_number_that_is_not_in_the_queue_still_opens_its_account(self):
        PushedLead.objects.all().delete()
        self.live("9222222222")
        d = self.poll()
        self.assertEqual((d["queue"], d["call"]["phase"]), ([], "ringing"))

    def test_start_from_scratch_clears_then_resends_only_this_agents_customers(self):
        self.fake.calls.clear()
        r = self.post(self.a1, "session/resend")
        self.assertEqual((r.status_code, r.json()["sent"], r.json()["total"], r.json()["warning"]), (200, 1, 1, ""))
        self.assertEqual([c[0] for c in self.fake.calls if c[0] in ("delete_leads", "bulk_add_leads")], ["delete_leads", "bulk_add_leads"])
        self.assertEqual([c for c in self.fake.calls if c[0] == "delete_leads"][0][2:], ("Account ID", ["L1"]))
        self.assertEqual(PushedLead.objects.count(), 1)

    def test_resend_still_sends_when_smartflo_cannot_clear(self):
        self.fake.fail = {"delete_leads": SmartfloError("Smartflo refused the request (404).")}
        r = self.post(self.a1, "session/resend").json()
        self.assertEqual((r["sent"], "would not clear" in r["warning"]), (1, True))

    def test_resend_needs_a_running_campaign(self):
        self.post(self.a1, "session/stop")
        self.assertEqual(self.post(self.a1, "session/resend").status_code, 400)

    def test_a_call_the_customer_cuts_at_once_is_marked_no_answer_by_itself_and_smartflo_is_told(self):
        self.live()
        self.poll()
        self.answered()
        self.poll()
        self.talked(8)                                           # answered, gone 8 seconds later
        self.fake.live = {"data": []}
        d = self.poll()
        self.assertEqual((d["awaiting_outcome"], d["queue"][0]["status_text"]), (False, "No answer (automatic)"))
        a = Activity.objects.get(loan=self.l1, kind="disposition")
        self.assertIn("lasted only 8 seconds", a.detail)
        self.assertEqual(self.fake.calls[-1], ("disposition", "agent-token", "c-1", "555"))
        self.assertTrue(DialAttempt.objects.get().outcome_sent)
        self.poll()
        self.assertEqual(Activity.objects.filter(loan=self.l1, kind="disposition").count(), 1)          # once only
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "disposition"]), 1)

    def test_a_real_conversation_is_not_marked_and_the_switch_can_be_turned_off(self):
        self.live()
        self.poll()
        self.answered()
        self.poll()
        self.talked(45)
        self.fake.live = {"data": []}
        d = self.poll()
        self.assertEqual((d["awaiting_outcome"], d["queue"][0]["status"]), (True, "outcome_pending"))
        self.assertFalse(Activity.objects.filter(loan=self.l1, kind="disposition").exists())

    def test_nothing_is_marked_when_smartflos_live_list_could_not_be_read(self):
        self.live()
        self.poll()
        self.answered()
        self.poll()
        self.talked(5)
        self.fake.fail = {"live_calls": SmartfloError("Smartflo refused the request (500).")}
        d = self.poll()
        self.assertFalse(Activity.objects.filter(loan=self.l1, kind="disposition").exists())            # a hiccup is not a hang-up
        self.assertEqual(DialAttempt.objects.get().state, "connected")

    def test_an_outcome_the_agent_saved_first_is_not_replaced_by_the_automatic_one(self):
        self.live()
        self.poll()
        self.answered()
        self.poll()
        self.talked(6)
        self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "wrong_number"})
        self.fake.live = {"data": []}
        self.poll()
        self.assertEqual(list(Activity.objects.filter(loan=self.l1, kind="disposition").values_list("disposition", flat=True)), ["wrong_number"])

    def test_an_outcome_goes_to_the_call_it_belongs_to_even_when_the_next_call_is_already_live(self):
        """Smartflo can ring the next customer the second a call ends; the outcome the agent is writing is still for the earlier call."""
        from dialer.models import DialerSession
        sess = DialerSession.objects.get()
        now = timezone.now()
        DialAttempt.objects.create(session=sess, loan=self.l1, call_id="old-call", state="ended", started_at=now - timedelta(minutes=2),
                                   connected_at=now - timedelta(minutes=2), ended_at=now - timedelta(minutes=1))
        DialerSession.objects.filter(pk=sess.pk).update(last_unique_id="new-call", last_loan=self.l2, released=False)
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "no_answer"}).json()
        self.assertEqual((r["released"], r["warning"]), (True, ""))
        self.assertEqual(self.fake.calls[-1], ("disposition", "agent-token", "old-call", "555"))      # not the live call's id
        sess.refresh_from_db()
        self.assertEqual((sess.released, sess.last_unique_id), (False, "new-call"))                   # the live call is still owed its own outcome
        self.assertTrue(DialAttempt.objects.get(call_id="old-call").outcome_sent)

    def test_a_refused_outcome_is_retried_for_its_own_call_once_smartflo_has_a_number(self):
        from dialer.models import DialerSession
        sess = DialerSession.objects.get()
        now = timezone.now()
        DialAttempt.objects.create(session=sess, loan=self.l1, call_id="old-call", state="ended", started_at=now - timedelta(minutes=2),
                                   connected_at=now - timedelta(minutes=2), ended_at=now - timedelta(minutes=1))
        DialerSession.objects.filter(pk=sess.pk).update(last_unique_id="new-call", last_loan=self.l2, released=False)
        DispositionMap.objects.filter(crm_code="no_answer").update(smartflo_status_id="")
        DispositionMap.objects.exclude(crm_code="no_answer").update(smartflo_status_id="")
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "no_answer"}).json()
        self.assertFalse(r["released"])
        DispositionMap.objects.filter(crm_code="no_answer").update(smartflo_status_id="555")
        self.poll()                                                                                   # the Dialer screen's own poll retries
        self.assertIn(("disposition", "agent-token", "old-call", "555"), self.fake.calls)
        self.assertTrue(DialAttempt.objects.get(call_id="old-call").outcome_sent)

    def test_an_outcome_with_no_smartflo_id_borrows_a_similar_one_so_the_agent_is_released(self):
        DispositionMap.objects.update(smartflo_status_id="", smartflo_code="")
        DispositionMap.objects.filter(crm_code="will_pay_no_date").update(smartflo_status_id="777")   # same group as "PTP given"
        self.live("9111111111")
        self.poll()
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "ptp_given", "ptp_date": str(self.day(2)), "ptp_reason": "forgot"}).json()
        self.assertEqual((r["released"], r["warning"]), (True, ""))
        self.assertEqual(self.fake.calls[-1], ("disposition", "agent-token", "c-1", "777"))
        self.assertEqual(r["loan"]["last_disposition"], "ptp_given")          # the CRM keeps the real outcome

    def test_end_call_finds_the_call_even_when_smartflos_agent_filter_shows_nothing(self):
        def live_calls(token, agent_number=None):
            return {"data": []} if agent_number else {"data": [{"call_id": "c-9", "customer_number": "9111111111", "status": "answered"}]}
        self.fake.live_calls = live_calls
        r = self.post(self.a1, "call/hangup", {"loan_id": self.l1.pk})
        self.assertEqual((r.status_code, self.fake.calls[-1]), (200, ("hangup", "agent-token", "c-9")))

    def test_clearing_tries_the_list_column_names_until_smartflo_accepts_one(self):
        seen = []

        def delete_leads(list_id, field, values):
            seen.append((field, list(values)))
            if field == "Account ID":
                raise SmartfloError("Smartflo refused the request (422). The selected field is invalid.")
            return {}
        self.fake.delete_leads = delete_leads
        r = self.post(self.a1, "session/resend").json()
        self.assertEqual((r["sent"], r["warning"]), (1, ""))
        self.assertEqual(seen, [("Account ID", ["L1"]), ("Phone Number", ["9111111111"])])

    def test_smartflos_own_view_of_my_customers(self):
        self.fake.leads = {"data": [{"field_0": "9111111111", "field_1": "Asha Rao", "field_2": "L1", "dial_status": 4, "dialer_dial_status": -1,
                                     "call_count": 2, "retry_count": 1, "lead_picked": 1},
                                    {"field_0": "9999999999", "field_2": "SOMEONE-ELSE", "dial_status": 1}]}
        d = self.api(self.a1).get("/api/dialer/session/leads").json()
        self.assertEqual(d["missing"], [])
        self.assertEqual(d["leads"], [{"account_id": "L1", "borrower_name": "Asha Rao", "dial_status": "No answer", "dialer_status": "Undisposed",
                                       "call_count": 2, "retry_count": 1, "picked": 1}])
        self.assertNotIn("9111111111", str(d))
        self.fake.leads = {"data": []}
        self.assertEqual(self.api(self.a1).get("/api/dialer/session/leads").json()["missing"], ["L1"])

    def test_clearing_uses_smartflos_own_lead_ids_when_they_can_be_read(self):
        self.fake.leads = {"data": [{"lead_id": "abc123", "field_0": "9111111111", "field_2": "L1"}, {"lead_id": "zzz", "field_0": "9000000000", "field_2": "OTHER"}]}
        seen = []

        def delete_leads(list_id, field, values):
            seen.append((field, list(values)))
            if field == "lead_id":
                raise SmartfloError("Smartflo refused the request (422). The selected field is invalid.")
            return {}
        self.fake.delete_leads = delete_leads
        r = self.post(self.a1, "session/resend").json()
        self.assertEqual((r["sent"], r["warning"]), (1, ""))
        self.assertEqual(seen, [("lead_id", ["abc123"]), ("id", ["abc123"])])           # only this agent's lead, never the other one's

    def test_clearing_falls_back_to_one_lead_at_a_time_and_reports_what_smartflo_said(self):
        self.fake.leads = {"data": [{"lead_id": "abc123", "field_0": "9111111111", "field_2": "L1"}]}
        self.fake.fail = {"delete_leads": SmartfloError("Smartflo refused the request (422). bad field")}
        r = self.post(self.a1, "session/resend").json()
        self.assertEqual((r["warning"] == "", ("delete_lead", "abc123") in self.fake.calls), (True, True))       # the one-by-one delete worked
        self.fake.calls.clear()
        self.fake.fail["delete_lead"] = SmartfloError("Smartflo refused the request (404).")
        r = self.post(self.a1, "session/resend").json()
        self.assertEqual(r["sent"], 1)
        self.assertIn("delete one lead: Smartflo refused the request (404)", r["warning"])
        self.assertIn("delete by Account ID", r["warning"])

    def test_the_dial_rules_are_sent_to_campaigns_that_already_exist(self):
        from dialer.setup import CAMPAIGN_DEFAULTS
        self.assertIn("-1", CAMPAIGN_DEFAULTS["dial_status"])        # customers Smartflo tried once (Undisposed) are dialled again
        self.fake.calls.clear()
        r = self.api(self.admin).post("/api/dialer/smartflo/rules/dpd1_7", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((len(r.json()["updated"]), r.json()["failed"]), (1, []))
        call = [c for c in self.fake.calls if c[0] == "update_campaign"][0]
        self.assertEqual((call[1], call[2]["dial_status"], call[2]["number_of_retry"]), ("111", CAMPAIGN_DEFAULTS["dial_status"], "3"))
        self.assertEqual(self.api(self.a1).post("/api/dialer/smartflo/rules/dpd1_7", {}, format="json").status_code, 403)
        self.fake.fail = {"update_campaign": SmartfloError("Smartflo refused the request (422).")}
        r = self.api(self.admin).post("/api/dialer/smartflo/rules/all", {}, format="json").json()
        self.assertEqual((r["updated"], len(r["failed"])), ([], 1))

    def outcome_lists(self):
        from dialer.setup import OUTCOME_LIST
        from loans.workflow import DISPOSITIONS
        items = [{"id": 100 + i, "name": label, "code": code} for i, (code, (label, _g, _s)) in enumerate(DISPOSITIONS.items())]
        self.fake.list_disposition_lists = lambda: {"data": [{"id": 9, "name": OUTCOME_LIST, "statuses": items}]}

    def test_when_no_outcome_has_a_smartflo_id_the_crm_makes_them_itself_and_releases_the_agent(self):
        DispositionMap.objects.update(smartflo_status_id="", smartflo_code="")          # nobody ever ran the outcomes set-up
        self.outcome_lists()
        self.live("9111111111")
        self.poll()
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "paid_today"}).json()
        self.assertEqual((r["released"], r["warning"]), (True, ""))
        self.assertEqual(self.fake.calls[-1][0], "disposition")
        self.assertTrue(DispositionMap.objects.filter(crm_code="paid_today").exclude(smartflo_status_id="").exists())

    def test_an_outcome_saved_earlier_is_passed_on_by_itself_once_an_id_exists(self):
        DispositionMap.objects.update(smartflo_status_id="", smartflo_code="")
        self.answered()
        self.poll()
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "paid_today"}).json()
        self.assertEqual(r["released"], False)                          # nothing could be mapped
        self.assertTrue(self.poll()["awaiting_outcome"])
        self.fake.live = {"data": []}
        DispositionMap.objects.filter(crm_code="paid_today").update(smartflo_status_id="321")      # the Admin maps it later
        self.fake.calls.clear()
        d = self.poll()
        self.assertEqual((d["awaiting_outcome"], ("disposition", "agent-token", "c-1", "321") in self.fake.calls), (False, True))

    def test_short_codes_alone_are_never_sent_because_smartflo_wants_a_number(self):
        self.answered()
        self.poll()
        DispositionMap.objects.update(smartflo_status_id="")            # codes stay (they are saved by the migration)
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "paid_today"}).json()
        self.assertEqual(r["released"], False)
        self.assertIn("has no number", r["warning"])
        self.assertFalse([c for c in self.fake.calls if c[0] == "disposition"])
        DispositionMap.objects.update(smartflo_status_id="abc")         # not a number either
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "paid_today"}).json()
        self.assertFalse([c for c in self.fake.calls if c[0] == "disposition"])

    def test_outcome_ids_are_read_from_smartflos_outcome_list(self):
        from dialer import setup as st
        self.fake.list_disposition_lists = lambda: {"data": [{"id": 9, "name": st.OUTCOME_LIST}]}      # the list reply shows no outcomes
        self.fake.create_disposition_list = lambda *a: {}
        self.fake.add_disposition_status = lambda *a: (_ for _ in ()).throw(SmartfloError("(422) The name has already been taken."))
        self.fake.list_disposition_statuses = lambda list_id: {"data": [
            {"id": 71001, "name": "No answer", "code": "noans"}, {"id": 71002, "name": "Connected paid today", "code": "paid"}]}
        DispositionMap.objects.update(smartflo_status_id="")
        res = st.provision_outcomes(self.fake)
        self.assertEqual(res["mapped"], 2)
        self.assertEqual(DispositionMap.objects.get(crm_code="no_answer").smartflo_status_id, "71001")
        self.assertEqual(DispositionMap.objects.get(crm_code="paid_today").smartflo_status_id, "71002")

    def test_a_refused_outcome_list_read_is_reported_not_fatal(self):
        from dialer import setup as st
        self.fake.list_disposition_lists = lambda: {"data": [{"id": 9, "name": st.OUTCOME_LIST}]}
        self.fake.create_disposition_list = lambda *a: {}
        self.fake.add_disposition_status = lambda *a: (_ for _ in ()).throw(SmartfloError("(422) The name has already been taken."))

        def refuse(list_id):
            raise SmartfloError("Smartflo refused the request (404). Not found")
        self.fake.list_disposition_statuses = refuse
        res = st.provision_outcomes(self.fake)
        self.assertTrue(any("Could not read the outcome ids" in x for x in res["steps"]))
        self.assertTrue(any("NUMBER" in x for x in res["steps"]))

    def test_create_outcomes_accepts_ones_already_made_by_hand(self):
        from dialer import setup as st
        calls = []
        self.fake.list_disposition_lists = lambda: {"data": [{"id": 9, "name": st.OUTCOME_LIST}]}      # statuses are not shown in the reply
        self.fake.create_disposition_list = lambda *a: {}

        def add(list_id, name, code):
            calls.append(code)
            raise SmartfloError("Smartflo refused the request (422). {'name': ['The name has already been taken.']}")
        self.fake.add_disposition_status = add
        res = st.provision_outcomes(self.fake)
        self.assertEqual((len(calls), res["mapped"], len(res["missing"])), (11, 0, 11))
        self.assertTrue(any("NUMBER" in x for x in res["steps"]))
        self.assertEqual(DispositionMap.objects.get(crm_code="no_answer").smartflo_code, "noans")
