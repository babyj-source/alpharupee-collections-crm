from dialer.models import DialAttempt, DispositionMap, PushedLead
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
