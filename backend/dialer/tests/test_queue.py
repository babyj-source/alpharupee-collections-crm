from dialer.models import DialAttempt, DispositionMap, PushedLead
from dialer.smartflo import SmartfloError
from dialer.tests.test_dialer import DialerBase


class QueueTests(DialerBase):
    def setUp(self):
        super().setUp()
        DispositionMap.objects.filter(crm_code="no_answer").update(smartflo_status_id="555")
        self.start()

    def poll(self):
        return self.api(self.a1).get("/api/dialer/session").json()

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
        self.assertEqual((d["queue"][0]["status"], d["awaiting_outcome"], d["log"][0]["result"], d["queue"][0]["attempts"]), ("no_answer", False, "Not answered", 1))
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

    def test_an_outcome_with_no_smartflo_id_borrows_a_similar_one_so_the_agent_is_released(self):
        DispositionMap.objects.filter(crm_code="no_answer").update(smartflo_status_id="")
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
