from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from accounts.models import AuditLog
from dialer import views
from dialer.models import Campaign, CampaignPush, DialerProfile, DialerSession, DispositionMap, PushedLead
from dialer.pools import lead_rows, pool_loans
from dialer.smartflo import SmartfloClient, SmartfloDisabled, SmartfloError, last10, parse_live_calls
from loans.models import Activity, CallAccess, Loan, Ptp
from loans.tests.test_workflow import Base

ON = override_settings(SMARTFLO_ENABLED=True, SMARTFLO_API_TOKEN="acct-token")


class FakeSmartflo:
    def __init__(self):
        self.calls, self.live, self.fail, self.in_session = [], {"data": []}, {}, False

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]
        return {}

    def agent_login(self, email, password):
        self._do("agent_login", email)  # the password is deliberately not recorded
        return "agent-token"

    def bulk_add_leads(self, list_id, rows):
        return self._do("bulk_add_leads", list_id, rows)

    leads = {"data": []}

    def list_leads(self, list_id, number=None):
        self._do("list_leads", list_id)
        return self.leads

    def update_campaign(self, campaign_id, body):
        return self._do("update_campaign", campaign_id, body)

    def delete_lead(self, lead_id):
        return self._do("delete_lead", lead_id)

    def delete_leads(self, list_id, field, values):
        return self._do("delete_leads", list_id, field, list(values))

    def probe(self):
        self._do("probe")
        return {"success": True, "data": []}

    def session_create(self, token, campaign_id, name):
        return self._do("session_create", token, campaign_id)

    def session_call(self, token, campaign_id, start=True):
        return self._do("session_call", token, campaign_id, start)

    def logout(self, token, campaign_id):
        return self._do("logout", token, campaign_id)

    def disposition(self, token, unique_id, status_id):
        return self._do("disposition", token, unique_id, status_id)

    def live_calls(self, token, agent_number=None):
        self._do("live_calls", agent_number)
        return self.live

    def click_to_call(self, token, agent_number, destination, in_session=False):
        self.in_session = in_session
        return self._do("click_to_call", token, agent_number, destination)

    def refresh(self, token):
        self._do("refresh", token)
        return "agent-token-2"

    def hangup(self, token, call_id):
        return self._do("hangup", token, call_id)

    def names(self):
        return [c[0] for c in self.calls]


@ON
class DialerBase(Base):
    def setUp(self):
        super().setUp()
        self.admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")
        self.fake = FakeSmartflo()
        for p in (mock.patch.object(views, "get_client", return_value=self.fake), mock.patch.object(views, "in_window", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        DialerProfile.objects.create(user=self.a1, smartflo_email="asha@smartflo.test", agent_number="0606000001")
        DialerProfile.objects.create(user=self.a2, smartflo_email="ravi@smartflo.test", agent_number="0606000002")
        Campaign.objects.filter(key="dpd1_7").update(smartflo_campaign_id="111", smartflo_list_id="222", active=True)

    def post(self, user, path, body=None):
        return self.api(user).post(f"/api/dialer/{path}", body or {}, format="json")

    def start(self, user=None, campaign="dpd1_7"):
        return self.post(user or self.a1, "session/start", {"campaign": campaign, "smartflo_password": "Sm@rtflo-pass"})

    def live(self, number="9111111111", cid="c-1"):
        self.fake.live = {"data": [{"call_id": cid, "customer_number": number, "status": "ringing"}]}


class PoolTests(DialerBase):
    def test_seeded_campaigns(self):
        self.assertEqual(list(Campaign.objects.values_list("key", flat=True)), ["dpd0", "dpd1_7", "dpd8_15", "dpd16", "ptp", "callback"])
        self.assertEqual(DispositionMap.objects.count(), 11)

    def keys(self, key):
        return sorted(pool_loans(key).values_list("account_id", flat=True))

    def test_buckets_and_precedence(self):
        self.assertEqual((self.keys("dpd1_7"), self.keys("dpd16"), self.keys("dpd0"), self.keys("ptp"), self.keys("callback")),
                         (["L1"], ["L2"], [], [], []))       # L3 owes nothing, L4 is closed
        Ptp.objects.create(loan=self.l1, agent=self.a1, promised_date=self.day(3), amount=100, reason="forgot")
        self.assertEqual((self.keys("dpd1_7"), self.keys("ptp")), ([], []))   # a promise still to come: held back
        Ptp.objects.filter(loan=self.l1).update(promised_date=self.day(0))
        self.assertEqual((self.keys("dpd1_7"), self.keys("ptp")), ([], ["L1"]))  # due today: promise pool
        Loan.objects.filter(pk=self.l1.pk).update(callback_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual((self.keys("ptp"), self.keys("callback")), ([], ["L1"]))  # a call-back beats a promise
        Loan.objects.filter(pk=self.l1.pk).update(callback_at=timezone.now() + timedelta(days=2))
        self.assertEqual((self.keys("ptp"), self.keys("callback"), self.keys("dpd1_7")), ([], [], []))  # future call-back: held

    def test_paid_and_dispute_and_no_number_are_left_out(self):
        Loan.objects.filter(pk=self.l2.pk).update(crm_status="dispute")
        Loan.objects.filter(pk=self.l1.pk).update(borrower_mobile="")
        self.assertEqual((self.keys("dpd16"), self.keys("dpd1_7")), ([], []))

    def test_lead_rows(self):
        rows = lead_rows(pool_loans("dpd1_7"))
        self.assertEqual(rows, [{"field_0": "9111111111", "field_1": "Asha Rao", "field_2": "L1"}])
        Loan.objects.filter(pk=self.l1.pk).update(borrower_mobile="12345")
        self.assertEqual(lead_rows(pool_loans("dpd1_7")), [])

    def test_pool_counts_for_everyone(self):
        r = self.api(self.a1).get("/api/dialer/pools").json()
        self.assertTrue(r["enabled"])
        by = {p["key"]: p for p in r["pools"]}
        self.assertEqual((by["dpd1_7"]["eligible"], by["dpd16"]["eligible"], by["dpd1_7"]["configured"]), (1, 1, True))
        self.assertNotIn("L1", str(r))  # counts only, no customers


class StartPushTests(DialerBase):
    """Starting a campaign sends that agent's own customers to Smartflo. There is no manual push any more."""

    def test_no_manual_push_endpoint(self):
        self.assertEqual(self.post(self.head, "pools/dpd1_7/push", {"confirm": True}).status_code, 404)

    def test_start_pushes_only_this_agents_customers(self):
        Loan.objects.filter(pk=self.l2.pk).update(dpd=3, assigned_agent=self.a2)   # also in dpd1_7, but Agent Two's
        r = self.start()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["push"], {"sent": 1, "total": 1, "error": ""})
        push = [c for c in self.fake.calls if c[0] == "bulk_add_leads"]
        self.assertEqual(len(push), 1)
        self.assertEqual(push[0][1:], ("222", [{"field_0": "9111111111", "field_1": "Asha Rao", "field_2": "L1"}]))
        self.assertEqual(self.fake.names().index("session_call") < self.fake.names().index("bulk_add_leads"), True)  # session first, then leads
        self.assertTrue(CampaignPush.objects.filter(ok=True, leads=1, by=self.a1).exists())
        self.assertEqual(Campaign.objects.get(key="dpd1_7").last_pushed_count, 1)
        self.assertNotIn("9111111111", AuditLog.objects.get(action="campaign_pushed").detail)

    def test_nobody_twice_in_a_day_and_new_allocations_follow(self):
        self.start()
        self.fake.calls.clear()
        r = self.post(self.a1, "session/push")
        self.assertEqual((r.status_code, r.json()["sent"], r.json()["total"]), (200, 0, 0))
        self.assertNotIn("bulk_add_leads", self.fake.names())
        self.start()                                                           # starting again the same day: still not re-sent
        self.assertNotIn("bulk_add_leads", self.fake.names())
        Loan.objects.filter(pk=self.l2.pk).update(dpd=3, assigned_agent=self.a1)  # a new customer is allocated to Asha
        self.assertEqual(self.post(self.a1, "session/push").json()["sent"], 1)
        self.assertEqual(PushedLead.objects.count(), 2)

    def test_agent_with_no_customers_still_starts(self):
        r = self.start(self.a2)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["push"], {"sent": 0, "total": 0, "error": ""})
        self.assertNotIn("bulk_add_leads", self.fake.names())

    def test_push_error_keeps_the_session_and_can_be_retried(self):
        self.fake.fail = {"bulk_add_leads": SmartfloError("Smartflo refused the request (500).")}
        r = self.start()
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["push"]["sent"], "refused" in r.json()["push"]["error"]), (0, True))
        self.assertEqual(DialerSession.objects.filter(ended_at__isnull=True).count(), 1)
        self.assertTrue(CampaignPush.objects.filter(ok=False).exists())
        self.assertEqual(PushedLead.objects.count(), 0)
        self.fake.fail = {}
        self.assertEqual(self.post(self.a1, "session/push").json()["sent"], 1)

    def test_chunks_and_partial_failure(self):
        Loan.objects.filter(pk=self.l2.pk).update(dpd=3, assigned_agent=self.a1)
        with mock.patch.object(views, "CHUNK", 1):
            self.fake.fail = {}
            self.assertEqual(self.start().json()["push"]["sent"], 2)
            self.assertEqual(self.fake.names().count("bulk_add_leads"), 2)
        PushedLead.objects.all().delete()
        self.fake.calls.clear()
        orig = self.fake.bulk_add_leads
        state = {"n": 0}
        def flaky(list_id, rows):
            state["n"] += 1
            if state["n"] == 2:
                raise SmartfloError("Smartflo refused the request (500).")
            return orig(list_id, rows)
        self.fake.bulk_add_leads = flaky
        with mock.patch.object(views, "CHUNK", 1):
            r = self.post(self.a1, "session/push").json()
        self.assertEqual((r["sent"], r["total"], bool(r["error"])), (1, 2, True))
        self.assertEqual(PushedLead.objects.count(), 1)       # only what really went is remembered

    def test_push_needs_a_session_and_the_switch(self):
        self.assertEqual(self.post(self.a1, "session/push").status_code, 400)
        self.start()
        with override_settings(SMARTFLO_ENABLED=False):
            self.assertEqual(self.post(self.a1, "session/push").status_code, 400)


class CampaignTests(DialerBase):
    RULES = {"dpd_min": 3, "dpd_max": 10, "min_overdue": 50, "product_code": "", "hold_promises": True, "hold_callbacks": True}

    def create(self, user=None, **body):
        body.setdefault("label", "Early stage")
        body.setdefault("rules", self.RULES)
        return self.post(user or self.head, "campaigns", body)

    def test_head_creates_a_campaign_by_rules(self):
        r = self.create(smartflo_campaign_id="901", smartflo_list_id="902", active=True)
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["key"], d["custom"], d["active"], d["eligible"]), ("c_early_stage", True, True, 1))   # L1 is 5 DPD
        self.assertIn("DPD 3 to 10", d["summary"])
        self.assertTrue(AuditLog.objects.filter(action="campaign_created").exists())
        self.assertEqual(self.start(campaign="c_early_stage").status_code, 200)       # agents can start it straight away
        self.assertEqual(self.fake.calls[-1][1], "902")                                 # leads go to the new list

    def test_rules_decide_who_is_in(self):
        self.create(label="Big ones", rules={**self.RULES, "dpd_min": 0, "dpd_max": "", "min_overdue": 300})
        by = {p["key"]: p for p in self.api(self.head).get("/api/dialer/pools").json()["pools"]}
        self.assertEqual(by["c_big_ones"]["eligible"], 1)           # only L2 (420 overdue)
        self.assertEqual(by["c_big_ones"]["rules"]["dpd_max"], None)
        Ptp.objects.create(loan=self.l2, agent=self.a2, promised_date=self.day(3), amount=100, reason="forgot")
        by = {p["key"]: p for p in self.api(self.head).get("/api/dialer/pools").json()["pools"]}
        self.assertEqual(by["c_big_ones"]["eligible"], 0)           # promise to come: held back
        self.create(label="With promises", rules={**self.RULES, "dpd_min": 0, "dpd_max": "", "min_overdue": 300, "hold_promises": False})
        by = {p["key"]: p for p in self.api(self.head).get("/api/dialer/pools").json()["pools"]}
        self.assertEqual(by["c_with_promises"]["eligible"], 1)

    def test_validation_and_permissions(self):
        self.assertEqual(self.create(self.a1).status_code, 403)
        self.assertEqual(self.create(label="  ").status_code, 400)
        self.assertEqual(self.create(rules={"dpd_min": 10, "dpd_max": 3}).status_code, 400)
        self.assertEqual(self.create(rules={"dpd_min": "x"}).status_code, 400)
        self.assertEqual(self.create(active=True).status_code, 400)                    # cannot switch on without ids
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 400)                               # same name twice
        self.assertEqual(self.create(label="Early Stage 2").json()["key"], "c_early_stage_2")

    def test_edit_pause_and_delete(self):
        self.create(smartflo_campaign_id="901", smartflo_list_id="902", active=True)
        put = lambda user, key, body: self.api(user).put(f"/api/dialer/campaigns/{key}", body, format="json")
        r = put(self.head, "c_early_stage", {"label": "Early DPD", "rules": {**self.RULES, "dpd_min": 6}, "active": False})
        self.assertEqual((r.status_code, r.json()["label"], r.json()["active"], r.json()["eligible"]), (200, "Early DPD", False, 0))
        self.assertEqual(self.start(campaign="c_early_stage").status_code, 400)       # paused: agents cannot start it
        self.assertEqual(put(self.a1, "c_early_stage", {"active": True}).status_code, 403)
        # a built-in campaign keeps its name and rules but its ids and switch can be changed by the head
        r = put(self.head, "dpd0", {"label": "Renamed", "rules": {"dpd_min": 50}, "smartflo_campaign_id": "5", "smartflo_list_id": "6", "active": True})
        c = Campaign.objects.get(key="dpd0")
        self.assertEqual((r.status_code, c.label, c.rules, c.active), (200, "0 DPD", {}, True))
        dele = lambda user, key: self.api(user).delete(f"/api/dialer/campaigns/{key}")
        self.assertEqual(dele(self.head, "dpd0").status_code, 400)                     # built-ins stay
        self.assertEqual(dele(self.a1, "c_early_stage").status_code, 403)
        self.assertEqual(dele(self.head, "c_early_stage").status_code, 204)
        self.assertFalse(Campaign.objects.filter(key="c_early_stage").exists())

    def test_cannot_delete_a_campaign_agents_have_used(self):
        self.create(smartflo_campaign_id="901", smartflo_list_id="902", active=True)
        self.start(campaign="c_early_stage")
        self.assertEqual(self.api(self.head).delete("/api/dialer/campaigns/c_early_stage").status_code, 400)
        self.assertTrue(Campaign.objects.filter(key="c_early_stage").exists())

    def test_agents_see_their_own_counts_not_the_heads_details(self):
        self.create(smartflo_campaign_id="901", smartflo_list_id="902", active=True)
        a = {p["key"]: p for p in self.api(self.a1).get("/api/dialer/pools").json()["pools"]}
        self.assertEqual((a["dpd1_7"]["mine"], a["dpd16"]["mine"]), (1, 0))             # L2 belongs to Agent Two
        self.assertNotIn("smartflo_list_id", a["c_early_stage"])
        h = {p["key"]: p for p in self.api(self.head).get("/api/dialer/pools").json()["pools"]}
        self.assertEqual(h["c_early_stage"]["smartflo_list_id"], "902")
        self.assertNotIn("mine", h["dpd1_7"])


class LiveTests(DialerBase):
    def get(self, user=None):
        return self.api(user or self.head).get("/api/dialer/live")

    def test_head_only(self):
        self.assertEqual(self.get(self.a1).status_code, 403)
        self.assertEqual(self.api().get("/api/dialer/live").status_code, 403)
        self.assertEqual(self.get(self.admin).status_code, 200)

    def test_campaign_numbers_agents_and_feed(self):
        self.start()                                                  # Asha starts: L1 pushed
        self.live(); self.api(self.a1).get("/api/dialer/session")   # she is on a call with L1
        r = self.get().json()
        c = {x["key"]: x for x in r["campaigns"]}["dpd1_7"]
        self.assertEqual((c["waiting"], c["pushed_today"], c["called"], c["connected"]), (1, 1, 0, 0))
        ag = r["agents"][0]
        self.assertEqual((ag["name"], ag["campaign"], ag["state"], ag["account_id"]), ("Agent One", "1-7 DPD", "on_call", "L1"))
        # she logs a connected outcome
        self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "ptp_given", "ptp_date": str(self.day(2)), "ptp_reason": "forgot"})
        r = self.get().json()
        c = {x["key"]: x for x in r["campaigns"]}["dpd1_7"]
        self.assertEqual((c["called"], c["connected"], c["promises"], c["paid"]), (1, 1, 1, 0))
        self.assertEqual((r["feed"][0]["agent"], r["feed"][0]["account_id"], r["feed"][0]["outcome"], r["feed"][0]["connected"]),
                         ("Agent One", "L1", "Connected · PTP given", True))
        self.assertEqual(r["agents"][0]["outcomes_today"], 1)
        self.assertNotIn("9111111111", str(r))                         # no phone numbers

    def test_agent_states(self):
        self.start()
        self.assertEqual(self.get().json()["agents"][0]["state"], "idle")            # never checked in
        self.api(self.a1).get("/api/dialer/session")
        self.assertEqual(self.get().json()["agents"][0]["state"], "waiting")
        DialerSession.objects.update(last_seen_at=timezone.now() - timedelta(minutes=2), on_call=True)
        self.assertEqual(self.get().json()["agents"][0]["state"], "idle")            # screen closed mid-call
        self.post(self.a1, "session/stop")
        self.assertEqual(self.get().json()["agents"], [])

    def test_nothing_pushed_means_zeros(self):
        r = self.get().json()
        self.assertTrue(all(c["pushed_today"] == 0 and c["called"] == 0 for c in r["campaigns"]))
        self.assertEqual((r["agents"], r["feed"]), ([], []))


class SetupTests(DialerBase):
    def test_admin_only(self):
        for u in (self.head, self.a1):
            self.assertEqual(self.api(u).get("/api/dialer/setup").status_code, 403)
        r = self.api(self.admin).get("/api/dialer/setup").json()
        self.assertEqual((r["status"], len(r["dispositions"])), ({"enabled": True, "token_set": True, "caller_id_set": False, "outcome_list_id": ""}, 11))
        self.assertNotIn("acct-token", str(r))
        self.assertEqual({a["name"] for a in r["agents"]}, {"Agent One", "Agent Two"})

    def put(self, path, body):
        return self.api(self.admin).put(f"/api/dialer/{path}", body, format="json")

    def test_campaign_ids_and_activation(self):
        self.assertEqual(self.put("campaigns/dpd0", {"active": True}).status_code, 400)  # no ids yet
        self.assertEqual(self.put("campaigns/dpd0", {"smartflo_campaign_id": "12ab"}).status_code, 400)
        self.assertEqual(self.put("campaigns/dpd0", {"smartflo_campaign_id": "390326", "smartflo_list_id": "259103", "active": True}).status_code, 200)
        c = Campaign.objects.get(key="dpd0")
        self.assertEqual((c.smartflo_campaign_id, c.smartflo_list_id, c.active, c.updated_by), ("390326", "259103", True, self.admin))
        self.assertEqual(self.put("campaigns/zzz", {}).status_code, 404)

    def test_disposition_map_and_agent_profile(self):
        self.assertEqual(self.put("dispositions", {"map": {"ptp_given": "71296", "no_answer": ""}}).status_code, 200)
        self.assertEqual(DispositionMap.objects.get(crm_code="ptp_given").smartflo_status_id, "71296")
        self.assertEqual(self.put("dispositions", {"map": {"ptp_given": "abc"}}).status_code, 400)
        self.assertEqual(self.put("dispositions", {"map": {"nonsense": "1"}}).status_code, 400)
        self.assertEqual(self.put(f"agents/{self.a1.pk}", {"smartflo_email": "new@smartflo.test", "agent_number": "0606999"}).status_code, 200)
        p = DialerProfile.objects.get(user=self.a1)
        self.assertEqual((p.smartflo_email, p.agent_number), ("new@smartflo.test", "0606999"))
        self.assertEqual(self.put(f"agents/{self.a1.pk}", {"agent_number": "06 06"}).status_code, 400)

    def test_smartflo_login_can_be_a_plain_id_not_only_an_email(self):
        put = lambda body: self.api(self.admin).put(f"/api/dialer/agents/{self.a1.pk}", body, format="json")
        self.assertEqual(put({"smartflo_email": "0607929520011", "agent_number": "0607929520011"}).status_code, 200)
        self.assertEqual(put({"smartflo_email": "AR admin"}).status_code, 400)            # spaces would never match
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.fake.calls[0][:2], ("agent_login", "0607929520011"))        # the id is what is sent to Smartflo

    def test_check_connection(self):
        self.assertEqual(self.post(self.admin, "check").json(), {"ok": True, "fields": ["data", "success"]})
        self.fake.fail = {"probe": SmartfloError("Smartflo refused the request (401).")}
        self.assertFalse(self.post(self.admin, "check").json()["ok"])
        self.assertEqual(self.post(self.head, "check").status_code, 403)


class SessionTests(DialerBase):
    def test_start_flow_and_secrets(self):
        r = self.start()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.fake.names(), ["agent_login", "session_create", "session_call", "bulk_add_leads"])
        self.assertEqual(self.fake.calls[0][1], "asha@smartflo.test")
        self.assertEqual(self.fake.calls[1][1:], ("agent-token", "111"))
        s = DialerSession.objects.get()
        self.assertNotIn("agent-token", s.token_enc)
        self.assertEqual(views.unseal(s.token_enc), "agent-token")
        dump = str(list(AuditLog.objects.values())) + r.content.decode()
        self.assertNotIn("Sm@rtflo-pass", dump)
        self.assertNotIn("agent-token", dump)
        self.assertTrue(AuditLog.objects.filter(action="dialer_started").exists())

    def test_start_refusals(self):
        self.assertEqual(self.start(campaign="dpd16").status_code, 400)                       # not switched on
        self.assertEqual(self.post(self.a1, "session/start", {"campaign": "dpd1_7"}).status_code, 400)  # no password
        self.assertEqual(self.post(self.a1, "session/start", {"campaign": "bad", "smartflo_password": "x"}).status_code, 400)
        Loan.objects.update()  # no-op, keeps the intent obvious
        u = type(self.head).objects.create_user("n@x.com", "pw", name="No Map", mobile="919000000010", role="agent")
        self.assertEqual(self.start(u).status_code, 400)                                      # not mapped to Smartflo
        with mock.patch.object(views, "in_window", return_value=False):
            self.assertEqual(self.start().status_code, 400)
        with override_settings(SMARTFLO_ENABLED=False):
            self.assertEqual(self.start().status_code, 400)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(DialerSession.objects.count(), 0)

    def test_wrong_smartflo_password_and_outage(self):
        self.fake.fail = {"agent_login": SmartfloError("Smartflo refused the request (401). Invalid credentials", status=401)}
        r = self.start()
        self.assertEqual(r.status_code, 400)
        self.assertIn("did not accept", r.json()["detail"])
        self.fake.fail = {"agent_login": SmartfloError("Could not reach Smartflo.")}
        self.assertEqual(self.start().status_code, 502)
        self.fake.fail = {"session_call": SmartfloError("Smartflo refused the request (400). Agent is on a call.", status=400)}
        self.assertEqual(self.start().status_code, 502)
        self.assertEqual(DialerSession.objects.count(), 0)

    def test_state_matches_the_call_to_the_account(self):
        self.start()
        self.assertEqual(self.api(self.a1).get("/api/dialer/session").json()["call"], None)
        self.live("9111111111")
        d = self.api(self.a1).get("/api/dialer/session").json()
        self.assertEqual((d["active"], d["call"]["state"], d["awaiting_outcome"], d["current"]["account_id"]), (True, "ringing", True, "L1"))
        self.assertNotIn("9111111111", str(d))   # masked
        self.assertEqual(DialerSession.objects.get().last_unique_id, "c-1")
        self.fake.live = {"data": [{"call_id": "c-1", "customer_number": "9111111111", "status": "answered"}]}     # the customer picks up
        d = self.api(self.a1).get("/api/dialer/session").json()
        self.assertEqual((d["call"]["phase"], d["awaiting_outcome"]), ("connected", True))
        self.fake.live = {"data": []}             # call ended after a conversation, outcome still owed
        d = self.api(self.a1).get("/api/dialer/session").json()
        self.assertEqual((d["call"], d["awaiting_outcome"], d["current"]["account_id"]), (None, True, "L1"))

    def test_unknown_number_and_unparsed_shape(self):
        self.start()
        self.live("9000011111")
        d = self.api(self.a1).get("/api/dialer/session").json()
        self.assertEqual((d["call"]["state"], d["current"]), ("ringing", None))
        self.fake.live = {"data": [{"weird": "x", "shape": "y"}]}
        d = self.api(self.a1).get("/api/dialer/session").json()
        self.assertEqual(d["unparsed_fields"], ["shape", "weird"])

    def test_shared_pool_gives_temporary_access_only(self):
        self.start(self.a2)
        self.live("9111111111")                    # L1 belongs to Agent One
        self.assertEqual(self.api(self.a2).get(f"/api/loans/{self.l1.pk}").status_code, 404)
        d = self.api(self.a2).get("/api/dialer/session").json()
        self.assertEqual(d["current"]["account_id"], "L1")
        self.assertEqual(self.api(self.a2).get(f"/api/loans/{self.l1.pk}").status_code, 200)
        self.assertEqual(self.api(self.a2).get(f"/api/loans/{self.l1.pk}/timeline").status_code, 200)
        ids = [x["account_id"] for x in self.api(self.a2).get("/api/loans").json()["results"]]
        self.assertEqual(ids, ["L2"])              # lists never include it
        self.assertEqual(self.api(self.a2).get("/api/dashboard").json()["accounts"], 1)
        CallAccess.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.api(self.a2).get(f"/api/loans/{self.l1.pk}").status_code, 404)

    def test_outcome_saves_and_releases_the_agent(self):
        DispositionMap.objects.filter(crm_code="no_answer").update(smartflo_status_id="555")
        self.start()
        self.live()
        self.api(self.a1).get("/api/dialer/session")
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "no_answer"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["released"], r.json()["warning"], r.json()["loan"]["last_disposition"]), (True, "", "no_answer"))
        self.assertEqual(self.fake.calls[-1], ("disposition", "agent-token", "c-1", "555"))
        self.assertFalse(self.api(self.a1).get("/api/dialer/session").json()["awaiting_outcome"])

    def test_outcome_without_mapping_or_on_error_keeps_the_crm_record(self):
        DispositionMap.objects.update(smartflo_status_id="", smartflo_code="")
        self.start()
        self.live()
        self.api(self.a1).get("/api/dialer/session")
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "no_answer"}).json()
        self.assertFalse(r["released"])
        self.assertIn("no Smartflo disposition", r["warning"])
        self.assertEqual(Activity.objects.filter(loan=self.l1, kind="disposition").count(), 1)
        DispositionMap.objects.filter(crm_code="no_answer").update(smartflo_status_id="555")
        self.fake.fail = {"disposition": SmartfloError("Smartflo refused the request (500).")}
        self.assertFalse(self.post(self.a1, "session/release", {"code": "no_answer"}).json()["released"])
        self.fake.fail = {}
        self.assertTrue(self.post(self.a1, "session/release", {"code": "no_answer"}).json()["released"])
        self.assertEqual(Activity.objects.filter(loan=self.l1, kind="disposition").count(), 1)  # no double entry

    def test_outcome_validation_errors_pass_through(self):
        self.start()
        r = self.post(self.a1, f"session/outcome/{self.l1.pk}", {"code": "call_back_later"})
        self.assertEqual(r.status_code, 400)   # call-back needs a date
        self.assertEqual(self.post(self.a1, f"session/outcome/{self.l2.pk}", {"code": "no_answer"}).status_code, 404)  # not theirs

    def test_dismiss_and_stop(self):
        self.start()
        self.live()
        self.api(self.a1).get("/api/dialer/session")
        self.post(self.a1, "session/dismiss")
        self.assertFalse(self.api(self.a1).get("/api/dialer/session").json()["awaiting_outcome"])
        self.assertEqual(self.post(self.a1, "session/stop").json(), {"active": False})
        self.assertEqual(self.fake.calls[-1][0], "logout")
        self.assertFalse(self.api(self.a1).get("/api/dialer/session").json()["active"])
        self.assertTrue(DialerSession.objects.get().ended_at)

    def test_expired_token_ends_the_session(self):
        self.start()
        DialerSession.objects.update(token_expires_at=timezone.now() - timedelta(seconds=1))
        d = self.api(self.a1).get("/api/dialer/session").json()
        self.assertEqual((d["active"], d["expired"]), (False, True))

    def test_new_start_closes_the_old_session(self):
        self.start()
        self.start()
        self.assertEqual(DialerSession.objects.filter(ended_at__isnull=True).count(), 1)
        self.assertIn("logout", self.fake.names())

    def test_status_endpoint(self):
        self.assertEqual(self.api(self.a1).get("/api/dialer/status").json(),
                         {"enabled": True, "in_window": True, "window": "08:00-19:00", "agent_ready": True, "can_click_to_call": True})
        self.assertTrue(self.api(self.head).get("/api/meta").json()["dialer_enabled"])


class ClickToCallTests(DialerBase):
    def call(self, user=None, loan=None, which="borrower"):
        return self.post(user or self.a1, "call", {"loan_id": (loan or self.l1).pk, "which": which})

    def test_places_the_call_and_logs_without_the_number(self):
        r = self.call()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.fake.calls[-1], ("click_to_call", "acct-token", "0606000001", "9111111111"))
        act = Activity.objects.filter(loan=self.l1, kind="call").get()
        self.assertEqual(act.detail, "Call placed to borrower")
        self.assertNotIn("9111111111", act.detail + AuditLog.objects.get(action="call_placed").detail)
        self.assertNotIn("9111111111", r.content.decode())

    def test_uses_the_agents_token_during_a_session(self):
        self.start()
        self.call()
        self.assertEqual(self.fake.calls[-1][1], "agent-token")

    def test_reference_numbers_and_overrides(self):
        self.assertEqual(self.call(which="ref1").status_code, 200)
        self.assertEqual(self.fake.calls[-1][3], "9333333333")
        self.assertEqual(self.call(which="ref2").status_code, 400)   # no second reference

    def test_refusals(self):
        self.assertEqual(self.call(self.a2).status_code, 404)        # not their account
        self.assertEqual(self.call(loan=self.l4, user=self.head).status_code, 400)  # closed
        with mock.patch.object(views, "in_window", return_value=False):
            self.assertEqual(self.call().status_code, 400)
        DialerProfile.objects.filter(user=self.a1).update(agent_number="")
        self.assertEqual(self.call().status_code, 400)
        with override_settings(SMARTFLO_ENABLED=False):
            DialerProfile.objects.filter(user=self.a1).update(agent_number="1")
            self.assertEqual(self.call().status_code, 400)
        self.assertEqual(self.post(self.a1, "call", {"loan_id": self.l1.pk, "which": "mum"}).status_code, 400)
        self.assertNotIn("click_to_call", self.fake.names())

    def test_smartflo_error_is_reported_and_not_logged_as_a_call(self):
        self.fake.fail = {"click_to_call": SmartfloError("Smartflo refused the request (400). Agent is not logged in.", status=400)}
        r = self.call()
        self.assertEqual(r.status_code, 502)
        self.assertFalse(Activity.objects.filter(kind="call").exists())


# The shape Smartflo really returns for a live click-to-call (numbers changed).
LIVE = {"id": 1, "call_id": "HYD1-D11-1.522967", "type": "click-to-call", "source": "0606000001", "state": "Answered",
        "customer_number": "09111111111", "destination": "+919111111111", "call_time": "00:00:16", "agent_name": "Asha"}


class CallPanelTests(DialerBase):
    def status(self, user=None, loan=None):
        return self.api(user or self.a1).get(f"/api/dialer/call/status?loan={(loan or self.l1).pk}")

    def hangup(self, user=None, loan=None):
        return self.post(user or self.a1, "call/hangup", {"loan_id": (loan or self.l1).pk})

    def test_no_call_means_none(self):
        self.fake.live = []
        self.assertEqual(self.status().json(), {"state": "none", "seconds": 0})

    def test_connected_call_with_seconds_and_no_number(self):
        self.fake.live = [LIVE]
        r = self.status()
        self.assertEqual(r.json(), {"state": "connected", "seconds": 16})
        self.assertNotIn("9111111111", r.content.decode())
        self.assertNotIn("HYD1", r.content.decode())

    def test_ringing_state_is_not_connected(self):
        self.fake.live = [dict(LIVE, state="Ringing", call_time="00:00:02")]
        self.assertEqual(self.status().json(), {"state": "ringing", "seconds": 2})

    def test_a_call_to_someone_elses_number_is_not_shown_on_this_account(self):
        self.fake.live = [dict(LIVE, customer_number="09999999999")]
        self.assertEqual(self.status().json()["state"], "none")

    def test_status_refusals(self):
        self.assertEqual(self.status(self.a2).status_code, 404)           # not their account
        self.assertEqual(self.api().get(f"/api/dialer/call/status?loan={self.l1.pk}").status_code, 403)
        self.fake.fail = {"live_calls": SmartfloError("Could not reach Smartflo.")}
        self.assertEqual(self.status().status_code, 502)

    def test_hangup_uses_the_call_id_from_smartflo_not_the_browser(self):
        self.fake.live = [LIVE]
        r = self.post(self.a1, "call/hangup", {"loan_id": self.l1.pk, "call_id": "attacker-chosen"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.fake.calls[-1], ("hangup", "acct-token", "HYD1-D11-1.522967"))
        self.assertTrue(AuditLog.objects.filter(action="call_ended").exists())

    def test_hangup_with_no_live_call_or_smartflo_refusal(self):
        self.fake.live = []
        self.assertEqual(self.hangup().status_code, 400)
        self.assertNotIn("hangup", self.fake.names())
        self.fake.live = [LIVE]
        self.fake.fail = {"hangup": SmartfloError("Smartflo refused the request (404).", status=404)}
        r = self.hangup()
        self.assertEqual(r.status_code, 502)
        self.assertIn("End the call on your phone", r.json()["detail"])

    def test_hangup_not_for_someone_elses_account(self):
        self.fake.live = [LIVE]
        self.assertEqual(self.hangup(self.a2).status_code, 404)


class ClientTests(Base):
    class Http:
        def __init__(self, status=200, body=None):
            self.status, self.body, self.sent = status, body if body is not None else {}, None

        def request(self, method, url, headers=None, json=None, params=None, timeout=None):
            self.sent = dict(method=method, url=url, headers=headers, json=json, params=params, timeout=timeout)
            http = self

            class R:
                status_code = http.status

                def json(self):
                    if http.body == "html":
                        raise ValueError
                    return http.body
            return R()

    @ON
    def test_requests_follow_the_vendor_examples(self):
        h = self.Http(body={"access_token": "T1"})
        c = SmartfloClient(h)
        self.assertEqual(c.agent_login("a@b.test", "pw"), "T1")
        self.assertEqual((h.sent["method"], h.sent["url"], h.sent["headers"]["Authorization"]),
                         ("POST", "https://api-smartflo.tatateleservices.com/v1/auth/login", "acct-token"))
        self.assertEqual(h.sent["json"], {"email": "a@b.test", "password": "pw"})
        c.session_create("T1", "390326", "Test")
        self.assertEqual((h.sent["headers"]["Authorization"], h.sent["json"]), ("Bearer T1", {"campaign_id": 390326, "campaign_name": "Test"}))
        c.session_call("T1", "390326", start=True)
        self.assertEqual(h.sent["json"], {"startOrEnd": True, "campaignId": "390326", "logout": False})
        c.logout("T1", "390326")
        self.assertEqual((h.sent["url"].endswith("/v1/dialer/logout"), h.sent["json"]), (True, {"campaign_id": "390326"}))
        c.disposition("T1", "u-1", 153899)
        self.assertEqual((h.sent["headers"]["Authorization"], h.sent["json"]), ("T1", {"disposition_status": "153899", "unique_id": "u-1"}))
        c.live_calls("T1", "0606")
        self.assertEqual((h.sent["method"], h.sent["params"]), ("GET", {"agent_number": "0606"}))
        c.click_to_call("T1", "0606", "9876543210")
        self.assertEqual(h.sent["json"], {"async": 1, "agent_number": "0606", "destination_number": "9876543210"})   # plain click-to-call
        with override_settings(SMARTFLO_CALLER_ID="1600322818"):
            c.click_to_call("T1", "0606", "9876543210")
        self.assertEqual(h.sent["json"], {"agent_number": "0606", "destination_number": "9876543210", "caller_id": "1600322818",
                                          "async": 1, "call_timeout": 300})                     # the same body as Smartflo's example
        self.assertEqual(h.sent["headers"]["Authorization"], "T1")                              # token sent plain, not as Bearer
        with override_settings(SMARTFLO_CLICK_TO_CALL_TYPE="dialer"):
            c.click_to_call("T1", "0606", "9876543210")
        self.assertEqual(h.sent["json"]["type"], "dialer")
        c.bulk_add_leads("174743", [{"field_0": "9"}])
        self.assertEqual((h.sent["url"].endswith("/v1/broadcast/lead/174743"), h.sent["json"]["duplicate_option"]), (True, "overwrite"))

    @ON
    def test_click_to_call_200_with_a_failure_body_is_an_error_not_a_success(self):
        for body in ({"success": False, "message": "Agent is not logged in"}, {"status": "failed", "message": "Agent is not logged in"},
                     {"message": "Agent is not logged in"}):
            with self.assertRaisesMessage(SmartfloError, "Agent is not logged in"):
                SmartfloClient(self.Http(body=body)).click_to_call("T1", "0606", "9876543210")
        ok = SmartfloClient(self.Http(body={"success": True, "message": "call queued", "data": {"customer": "9876543210"}}))
        with self.assertLogs("dialer.smartflo", level="INFO") as cm:
            self.assertTrue(ok.click_to_call("T1", "0606", "9876543210")["success"])
        self.assertIn("call queued", cm.output[0])
        self.assertNotIn("9876543210", cm.output[0])       # the number never reaches the log

    @ON
    def test_hangup_request_and_live_call_parsing(self):
        h = self.Http(body={"success": True, "message": "ok"})
        SmartfloClient(h).hangup("T1", "HYD1-1")
        self.assertEqual((h.sent["method"], h.sent["url"].endswith("/v1/call/hangup"), h.sent["json"], h.sent["headers"]["Authorization"]),
                         ("POST", True, {"call_id": "HYD1-1"}, "T1"))
        with self.assertRaisesMessage(SmartfloError, "Smartflo did not end the call"):
            SmartfloClient(self.Http(body={"success": False, "message": "no such call"})).hangup("T1", "x")
        calls, unparsed = parse_live_calls([LIVE])
        self.assertEqual((calls, unparsed), ([{"id": "HYD1-D11-1.522967", "number": "9111111111", "state": "Answered", "seconds": 16}], []))

    @ON
    def test_session_missing_reply_is_shown_to_the_agent(self):
        h = self.Http(body={"message": "No agent session details were found, please check."})
        with self.assertRaisesMessage(SmartfloError, "No agent session details were found"):
            SmartfloClient(h).click_to_call("T1", "0606", "9876543210")
        ok = self.Http(body={"message": "Call originated"})            # a plain success message is not mistaken for a refusal
        self.assertEqual(SmartfloClient(ok).click_to_call("T1", "0606", "9876543210"), {"message": "Call originated"})

    class PickyHttp(Http):
        """Rejects the token unless it arrives in the form `want` ('plain' or 'bearer')."""
        def __init__(self, want, msg="Deleted or blacklisted token provided"):
            super().__init__()
            self.want, self.msg, self.n = want, msg, 0

        def request(self, method, url, headers=None, json=None, params=None, timeout=None):
            self.n += 1
            form = "bearer" if headers["Authorization"].startswith("Bearer ") else "plain"
            self.status, self.body = (200, {"ok": 1}) if form == self.want else (401, {"message": self.msg})
            return super().request(method, url, headers=headers, json=json, params=params, timeout=timeout)

    @ON
    def test_token_header_form_falls_back_and_is_remembered(self):
        SmartfloClient._prefer.clear()
        h = self.PickyHttp("plain")                 # the probe tries Bearer first, Smartflo wants it plain
        c = SmartfloClient(h)
        self.assertEqual(c.probe(), {"ok": 1})
        self.assertEqual(h.n, 2)
        c.probe()
        self.assertEqual(h.n, 3)                    # remembered: one call, not two
        SmartfloClient._prefer.clear()
        h = self.PickyHttp("bearer")                # a plain-first endpoint where Smartflo wants Bearer
        self.assertEqual(SmartfloClient(h).bulk_add_leads("1", [{"field_0": "9"}]), {"ok": 1})
        SmartfloClient._prefer.clear()

    @ON
    def test_a_dead_token_is_reported_after_both_forms_and_a_wrong_password_is_not_retried(self):
        SmartfloClient._prefer.clear()
        h = self.PickyHttp("never")
        with self.assertRaises(SmartfloError) as cm:
            SmartfloClient(h).probe()
        self.assertEqual((h.n, cm.exception.status), (2, 401))
        self.assertIn("Deleted or blacklisted token", str(cm.exception))
        self.assertIn("with and without", str(cm.exception))
        self.assertNotIn("acct-token", str(cm.exception))
        h = self.PickyHttp("never", msg="Invalid credentials")     # not a token problem: must not retry (login attempts are limited)
        with self.assertRaises(SmartfloError):
            SmartfloClient(h).agent_login("a@b.test", "pw")
        self.assertEqual(h.n, 1)
        SmartfloClient._prefer.clear()

    @ON
    def test_errors_never_carry_secrets(self):
        c = SmartfloClient(self.Http(status=401, body={"message": "Too many login attempts have been made for this account."}))
        with self.assertRaises(SmartfloError) as cm:
            c.agent_login("a@b.test", "secret-pw")
        self.assertEqual(cm.exception.status, 401)
        self.assertIn("Too many login attempts", str(cm.exception))
        self.assertNotIn("secret-pw", str(cm.exception))
        self.assertNotIn("acct-token", str(cm.exception))
        with self.assertRaises(SmartfloError):
            SmartfloClient(self.Http(body={})).agent_login("a@b.test", "pw")      # no token in the reply
        self.assertEqual(SmartfloClient(self.Http(body="html")).probe(), {})

    @override_settings(SMARTFLO_ENABLED=False, SMARTFLO_API_TOKEN="x")
    def test_off_switch_blocks_every_call(self):
        h = self.Http()
        with self.assertRaises(SmartfloDisabled):
            SmartfloClient(h).probe()
        self.assertIsNone(h.sent)

    @override_settings(SMARTFLO_ENABLED=True, SMARTFLO_API_TOKEN="")
    def test_no_token_no_call(self):
        h = self.Http()
        with self.assertRaises(SmartfloError):
            SmartfloClient(h).probe()
        self.assertIsNone(h.sent)

    def test_parse_live_calls(self):
        one = {"call_id": "c1", "customer_number": "+91 98765-43210", "status": "answered"}
        self.assertEqual(parse_live_calls([one])[0], [{"id": "c1", "number": "9876543210", "state": "answered", "seconds": 0}])
        self.assertEqual(parse_live_calls({"data": [one]})[0][0]["id"], "c1")
        self.assertEqual(parse_live_calls({"data": {"calls": [one]}})[0][0]["number"], "9876543210")
        self.assertEqual(parse_live_calls({"results": [{"uuid": "u", "destination": "9876543210"}]})[0][0]["id"], "u")
        self.assertEqual(parse_live_calls({"data": []}), ([], []))
        self.assertEqual(parse_live_calls({"success": True}), ([], []))
        calls, unparsed = parse_live_calls([{"foo": 1, "bar": "9876543210"}])
        self.assertEqual((calls, unparsed), ([], ["bar", "foo"]))
        self.assertEqual(last10("0091 98765 43210"), "9876543210")
        self.assertEqual(last10("123"), "")
