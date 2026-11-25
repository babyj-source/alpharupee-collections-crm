from unittest import mock

from django.test import override_settings

from dialer import views
from dialer.setup import clean_name
from dialer.models import Campaign, DispositionMap, SmartfloSetting
from dialer.smartflo import SmartfloClient, SmartfloError, find_id
from dialer.tests.test_dialer import ON, DialerBase, FakeSmartflo
from loans.tests.test_workflow import Base
from loans.workflow import DISPOSITIONS


class Account(FakeSmartflo):
    """A Smartflo account that remembers what is created, like the real one: creating answers only success, the ids appear in the lists."""

    def __init__(self):
        super().__init__()
        self.lead_lists, self.campaigns, self.dlists, self.statuses, self.next = [], [], [], [], 9000
        self.users = [{"id": 501, "name": "Asha", "agent": {"id": "0606000001"}, "login_id": "asha@smartflo.test"},
                      {"id": 502, "name": "Ravi", "agent": {"id": "0606000002"}, "login_id": "RAVI@smartflo.test"},
                      {"id": 503, "name": "Other", "agent": {"id": "0606000003"}, "login_id": "other@smartflo.test"}]

    def _id(self):
        self.next += 1
        return self.next

    def list_users(self, limit=200):
        self.calls.append(("list_users",))
        return {"has_more": False, "data": self.users}

    def list_lead_lists(self):
        return [dict(x) for x in self.lead_lists]

    def create_lead_list(self, name, description, fields):
        self._do("create_lead_list", name, fields)
        self.lead_lists.append({"id": self._id(), "name": name, "field_map": fields})
        return {"success": True, "message": "created"}

    def list_my_numbers(self):
        return [{"id": "546294", "name": "dialer", "alias": "0606000009", "did": "+911600322818", "destination": None}]

    def list_campaigns(self):
        return {"data": [dict(x) for x in self.campaigns]}

    def create_campaign(self, body):
        self._do("create_campaign", body)
        self.campaigns.append({"id": self._id(), "name": body["name"]})
        return {"success": True}

    def list_disposition_lists(self):
        return {"data": [{**d, "statuses": [s for s in self.statuses if str(s["list"]) == str(d["id"])]} for d in self.dlists]}

    def create_disposition_list(self, name, description):
        self._do("create_disposition_list", name)
        self.dlists.append({"id": self._id(), "name": name})
        return {"success": True}

    def add_disposition_status(self, list_id, name, code):
        self._do("add_disposition_status", list_id, name, code)
        self.statuses.append({"id": self._id(), "name": name, "code": code, "list": list_id})
        return {"success": True}


@ON
@override_settings(SMARTFLO_CALLER_ID="1600322818")
class SetupFlowTests(DialerBase):
    def setUp(self):
        super().setUp()
        self.fake = Account()
        p = mock.patch.object(views, "get_client", return_value=self.fake)
        p.start()
        self.addCleanup(p.stop)
        Campaign.objects.filter(key="dpd8_15").update(smartflo_campaign_id="", smartflo_list_id="")

    def test_outcomes_list_is_created_and_every_outcome_id_saved(self):
        r = self.post(self.admin, "smartflo/outcomes")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["mapped"], len(DISPOSITIONS))
        self.assertEqual(self.fake.names().count("add_disposition_status"), len(DISPOSITIONS))
        ids = set(DispositionMap.objects.values_list("smartflo_status_id", flat=True))
        self.assertEqual(len(ids), len(DISPOSITIONS))
        self.assertTrue(all(i.isdigit() for i in ids))
        self.assertEqual(SmartfloSetting.objects.get(key="disposition_list_id").value, str(self.fake.dlists[0]["id"]))
        self.assertIn("ptp_given", set(DispositionMap.objects.values_list("crm_code", flat=True)))

    def test_outcomes_run_twice_creates_nothing_twice(self):
        self.post(self.admin, "smartflo/outcomes")
        n = len(self.fake.statuses)
        r = self.post(self.admin, "smartflo/outcomes")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((len(self.fake.statuses), len(self.fake.dlists)), (n, 1))

    def test_campaign_makes_the_outcomes_list_itself_when_it_is_missing(self):
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.fake.names().count("add_disposition_status"), len(DISPOSITIONS))
        self.assertEqual(DispositionMap.objects.exclude(smartflo_status_id="").count(), len(DISPOSITIONS))
        body = next(c[1] for c in self.fake.calls if c[0] == "create_campaign")
        self.assertEqual(body["disposition_list"], str(self.fake.dlists[0]["id"]))
        self.assertTrue(any("outcomes list" in x for x in r.json()["steps"]))

    def test_campaign_creates_list_and_campaign_and_saves_ids(self):
        self.post(self.admin, "smartflo/outcomes")
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 200, r.content)
        camp = Campaign.objects.get(key="dpd8_15")
        self.assertEqual((camp.smartflo_list_id, camp.smartflo_campaign_id), (str(self.fake.lead_lists[0]["id"]), str(self.fake.campaigns[0]["id"])))
        self.assertTrue(camp.configured)
        self.assertEqual(self.fake.lead_lists[0]["field_map"], ["Phone Number", "Name", "Account ID"])
        body = next(c[1] for c in self.fake.calls if c[0] == "create_campaign")
        self.assertEqual(body["agent"], [501, 502])            # only the CRM agents that have a Smartflo login id; "Other" is left out
        self.assertEqual(body["outbound_caller_id"], ["546294"])          # the number's id in Smartflo, not the number itself
        self.assertEqual(body["lead_list_map"], [{"list_id": camp.smartflo_list_id, "list_status": "1"}])
        self.assertEqual((body["dial_method"], body["dial_in_type"], body["connect_agent_through"], body["queue_timeout"], body["ring_strategy"]),
                         ("2", "3", "2", "10", "1"))                                # what Smartflo's team tested
        self.assertEqual((body["hide_lead_details"], body["update_lead_details"], body["agent_only_callback"]), (True, True, True))
        self.assertEqual(body["disposition_list"], str(self.fake.dlists[0]["id"]))
        self.assertEqual((body["number_of_retry"], body["retry_after_minutes"], body["ring_timeout"]), ("3", "30", "30"))
        self.assertTrue(r.json()["campaign"]["configured"])

    def test_a_refused_campaign_says_which_step_and_keeps_the_list(self):
        from dialer.smartflo import SmartfloError as E
        self.post(self.admin, "smartflo/outcomes")
        self.fake.fail = {"create_campaign": E("Smartflo refused the request (422). The given data was invalid. agent: already in a campaign", status=422)}
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 400)
        self.assertIn("would not create the campaign", r.json()["detail"])
        self.assertIn("already in a campaign", r.json()["detail"])
        camp = Campaign.objects.get(key="dpd8_15")
        self.assertTrue(camp.smartflo_list_id)
        self.assertFalse(camp.smartflo_campaign_id)
        self.fake.fail = {}
        self.assertEqual(self.post(self.admin, "smartflo/campaign/dpd8_15").status_code, 200)       # try again after the fix
        self.assertEqual(len(self.fake.lead_lists), 1)                                              # the list was reused

    def test_names_sent_to_smartflo_have_no_special_characters(self):
        self.assertEqual(clean_name("8–15 days late", "CRM"), "CRM 8 15 days late")
        self.assertEqual(clean_name("Promise to pay (today) / PTP & more!", "CRM"), "CRM Promise to pay today PTP more")
        self.assertEqual(clean_name("  Zażółć  ", "CRM"), "CRM Za")
        self.assertEqual(len(clean_name("x" * 100, "CRM")), 60)
        self.post(self.admin, "smartflo/outcomes")
        Campaign.objects.filter(key="dpd8_15").update(label="8-15 DPD · early/late")
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 200, r.content)
        body = next(c[1] for c in self.fake.calls if c[0] == "create_campaign")
        self.assertEqual((body["name"], self.fake.lead_lists[0]["name"]), ("CRM 8 15 DPD early late", "CRM 8 15 DPD early late"))
        for text in (body["name"], body["description"]):
            self.assertRegex(text, r"^[A-Za-z0-9 ]+$")

    def test_a_refused_lead_list_says_so(self):
        from dialer.smartflo import SmartfloError as E
        self.post(self.admin, "smartflo/outcomes")
        self.fake.fail = {"create_lead_list": E("Smartflo refused the request (422). Name taken", status=422)}
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 400)
        self.assertIn("would not create the lead list", r.json()["detail"])

    def test_existing_items_in_smartflo_are_adopted_not_duplicated(self):
        self.post(self.admin, "smartflo/outcomes")
        self.fake.lead_lists.append({"id": 777, "name": "CRM 8–15 days late"})
        label = Campaign.objects.get(key="dpd8_15").label
        self.fake.lead_lists[0]["name"] = clean_name(label, "CRM")
        self.fake.campaigns.append({"id": 888, "name": clean_name(label, "CRM")})
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn("create_campaign", self.fake.names())
        self.assertNotIn("create_lead_list", self.fake.names())
        self.assertEqual(Campaign.objects.get(key="dpd8_15").smartflo_campaign_id, "888")

    def test_already_set_up_campaign_is_refused(self):
        self.post(self.admin, "smartflo/outcomes")
        r = self.post(self.admin, "smartflo/campaign/dpd1_7")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already", r.json()["detail"])

    def test_no_matching_agents_is_refused_with_a_reason(self):
        self.post(self.admin, "smartflo/outcomes")
        self.fake.users = [{"id": 1, "name": "x", "agent": {"id": "1"}, "login_id": "nobody@x.test"}]
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 400)
        self.assertIn("login id", r.json()["detail"])

    @override_settings(SMARTFLO_CALLER_ID="")
    def test_missing_caller_id_is_refused(self):
        self.post(self.admin, "smartflo/outcomes")
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 400)
        self.assertIn("SMARTFLO_CALLER_ID", r.json()["detail"])

    def test_id_not_found_after_create_is_reported_not_guessed(self):
        self.post(self.admin, "smartflo/outcomes")
        self.fake.create_lead_list = lambda *a: {"success": True}      # created, but never shows in the lists
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 400)
        self.assertIn("did not say which id", r.json()["detail"])
        self.assertFalse(Campaign.objects.get(key="dpd8_15").smartflo_list_id)

    def test_smartflo_refusal_is_shown(self):
        self.fake.fail["create_disposition_list"] = SmartfloError("Smartflo refused the request (400). name taken", status=400)
        r = self.post(self.admin, "smartflo/outcomes")
        self.assertEqual(r.status_code, 502)
        self.assertIn("name taken", r.json()["detail"])

    def test_only_admin_and_only_when_on(self):
        for who in (self.a1, self.head):
            self.assertEqual(self.post(who, "smartflo/outcomes").status_code, 403)
            self.assertEqual(self.post(who, "smartflo/campaign/dpd8_15").status_code, 403)
        with override_settings(SMARTFLO_ENABLED=False):
            self.assertEqual(self.post(self.admin, "smartflo/outcomes").status_code, 400)

    def test_users_list_for_matching_agents(self):
        r = self.api(self.admin).get("/api/dialer/smartflo/users")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["users"][0], {"user_id": 501, "name": "Asha", "login_id": "asha@smartflo.test", "agent_number": "0606000001"})
        self.assertEqual(self.api(self.a1).get("/api/dialer/smartflo/users").status_code, 403)

    def test_setup_screen_says_what_is_missing(self):
        s = self.api(self.admin).get("/api/dialer/setup").json()["status"]
        self.assertEqual((s["caller_id_set"], s["outcome_list_id"]), (True, ""))


class ClientSetupTests(Base):
    Http = __import__("dialer.tests.test_dialer", fromlist=["ClientTests"]).ClientTests.Http

    @ON
    def test_set_up_requests_follow_the_public_api_reference(self):
        h = self.Http(body={"success": True})
        c = SmartfloClient(h)
        c.create_lead_list("L", "d", ["Phone Number", "Name"])
        self.assertEqual((h.sent["method"], h.sent["url"].endswith("/v1/broadcast/list"), h.sent["json"]),
                         ("POST", True, {"name": "L", "description": "d", "field": ["Phone Number", "Name"], "enable_outbound_based_skill": "0"}))
        c.list_lead_lists()
        self.assertTrue(h.sent["url"].endswith("/v1/broadcast/lists"))
        c.create_campaign({"name": "C"})
        self.assertEqual((h.sent["url"], h.sent["json"], h.sent["headers"]["Authorization"]),
                         ("https://cloudphone.tatateleservices.com/api/v1/outbound-dialer/add-dialer-campaign", {"name": "C"}, "acct-token"))
        c.create_disposition_list("D", "x")
        self.assertEqual((h.sent["url"].endswith("/v1/dialer/disposition_list"), h.sent["json"]), (True, {"name": "D", "description": "x", "status": True}))
        c.add_disposition_status(12, "PTP", "ptp_given")
        self.assertEqual((h.sent["url"].endswith("/v1/dialer/disposition_status"), h.sent["json"]), (True, {"name": "PTP", "code": "ptp_given", "dispositionList": "12"}))
        c.delete_leads(5, "field_0", ["9876543210"])
        self.assertEqual((h.sent["method"], h.sent["url"].endswith("/v1/broadcast/leads/5"), h.sent["json"]), ("DELETE", True, {"field": "field_0", "data": ["9876543210"]}))
        c.list_users()
        self.assertEqual((h.sent["url"].endswith("/v1/users"), h.sent["params"]), (True, {"limit": 200}))

    @ON
    def test_outcome_is_saved_at_the_address_smartflo_gave(self):
        h = self.Http(body={"success": True})
        SmartfloClient(h).disposition("T", "u-1", 77)
        self.assertTrue(h.sent["url"].endswith("/v1/dialer/disposition_details_update"))
        self.assertEqual(h.sent["json"], {"disposition_status": "77", "unique_id": "u-1"})
        with self.assertRaisesMessage(SmartfloError, "Call not found"):
            SmartfloClient(self.Http(body={"success": False, "message": "Call not found"})).disposition("T", "u-1", 77)

    @ON
    def test_lists_are_read_page_by_page_so_nothing_that_exists_is_missed(self):
        pages = [{"has_more": True, "last_seen_id": 40, "data": [{"id": 1, "name": "A"}]}, {"has_more": False, "last_seen_id": 41, "data": [{"id": 2, "name": "CRM 8-15 DPD"}]}]
        sent = []

        class Http:
            def request(self, method, url, headers=None, json=None, params=None, timeout=None):
                sent.append(dict(params or {}))

                class R:
                    status_code = 200

                    def json(self_inner):
                        return pages[min(len(sent) - 1, 1)]
                return R()
        out = SmartfloClient(Http()).list_lead_lists()
        self.assertEqual(find_id(out, name="CRM 8-15 DPD"), "2")           # found on the second page
        self.assertEqual(sent, [{"limit": 100}, {"limit": 100, "last_seen_id": 40}])

    @ON
    def test_a_list_that_rejects_paging_inputs_is_asked_again_plainly(self):
        calls = []

        class Http:
            def request(self, method, url, headers=None, json=None, params=None, timeout=None):
                calls.append(params)

                class R:
                    status_code = 422 if params else 200

                    def json(self_inner):
                        return {"data": [{"id": 7, "name": "X"}]}
                return R()
        self.assertEqual(find_id(SmartfloClient(Http()).list_campaigns(), name="X"), "7")
        self.assertEqual(calls, [{"limit": 100}, None])

    @ON
    def test_an_unreadable_422_shows_the_start_of_the_reply(self):
        class Http:
            def request(self, method, url, headers=None, json=None, params=None, timeout=None):
                class R:
                    status_code = 422
                    text = "<html><body>Name already exists for this account</body></html>"

                    def json(self_inner):
                        raise ValueError
                return R()
        with self.assertRaises(SmartfloError) as cm:
            SmartfloClient(Http()).create_lead_list("L", "d", ["A"])
        self.assertIn("Name already exists for this account", str(cm.exception))

    @ON
    def test_a_422_shows_the_field_errors_smartflo_sent(self):
        body = {"success": False, "message": "The given data was invalid.", "errors": {"agent": ["The agent field is required."], "outbound_caller_id": ["Already in use."]}}
        with self.assertRaises(SmartfloError) as cm:
            SmartfloClient(self.Http(status=422, body=body)).create_lead_list("L", "d", ["A"])
        text = str(cm.exception)
        self.assertIn("(422)", text)
        self.assertIn("agent: The agent field is required.", text)
        self.assertIn("outbound_caller_id: Already in use.", text)

    @ON
    def test_the_campaign_name_sent_has_no_symbols_smartflo_refuses(self):
        """Production, 24 Nov 2026: "16+ DPD" answered 422 "The campaign name format is invalid."; "1-7 DPD" was accepted."""
        for label, sent in (("16+ DPD", "16 DPD"), ("1-7 DPD", "1-7 DPD"), ("0 DPD", "0 DPD"), ("Promise to pay", "Promise to pay"), ("Call-back (today)", "Call-back today")):
            h = self.Http(body={"ok": True})
            SmartfloClient(h).session_create("T", "605931", label)
            self.assertEqual(h.sent["json"]["campaign_name"], sent, label)

    @ON
    def test_a_session_that_already_exists_is_not_an_error(self):
        said = "You are already logged in to this campaign. Initiate session call to start dialing."
        r = SmartfloClient(self.Http(status=422, body={"success": False, "message": said})).session_create("T", "5", "C")
        self.assertTrue(r["already_logged_in"])
        with self.assertRaises(SmartfloError):                         # any other refusal still stops the start
            SmartfloClient(self.Http(status=422, body={"message": "Campaign is not active."})).session_create("T", "5", "C")

    @ON
    def test_being_signed_in_to_another_campaign_is_closed_and_the_start_retried(self):
        """Production, 20 Nov 2026: Smartflo said "only be logged in to one campaign at a time" and named the agent's other campaign."""
        said = {"ok": False, "error": "You can only be logged in to one campaign at a time. Please logout from the following campaign(s) before logging in again.",
                "data": [{"id": 577161, "name": "Collection Campaign"}]}
        sent = []

        class Http:
            def request(self, method, url, headers=None, json=None, params=None, timeout=None):
                sent.append((url.rsplit("/v1/", 1)[1], dict(json or {})))
                first_create = sum(1 for u, _ in sent if u == "dialer/session/create") == 1

                class R:
                    status_code = 422 if (url.endswith("session/create") and first_create) else 200

                    def json(self_inner):
                        return said if self_inner.status_code == 422 else {"success": True}
                return R()
        r = SmartfloClient(Http()).session_create("T", "605933", "1-7 DPD")
        self.assertEqual(r["left"], ["Collection Campaign"])
        self.assertEqual([u for u, _ in sent], ["dialer/session/create", "dialer/logout", "dialer/session/create"])
        self.assertEqual(sent[1][1], {"campaign_id": "577161"})

    @ON
    def test_if_the_other_campaign_cannot_be_closed_the_agent_is_told_which_one(self):
        said = {"ok": False, "error": "You can only be logged in to one campaign at a time.", "data": [{"id": 577161, "name": "Collection Campaign"}]}

        class Http:
            def request(self, method, url, headers=None, json=None, params=None, timeout=None):
                class R:
                    status_code = 422 if url.endswith("session/create") else 500

                    def json(self_inner):
                        return said if self_inner.status_code == 422 else {"message": "boom"}
                return R()
        with self.assertRaises(SmartfloError) as cm:
            SmartfloClient(Http()).session_create("T", "605933", "1-7 DPD")
        self.assertIn("Collection Campaign", str(cm.exception))
        self.assertIn("Sign out of it in Smartflo", str(cm.exception))

    @ON
    def test_campaign_refusal_in_a_200_is_an_error(self):
        with self.assertRaisesMessage(SmartfloError, "Invalid caller"):
            SmartfloClient(self.Http(body={"success": False, "message": "Invalid caller"})).create_campaign({"name": "C"})

    @ON
    def test_session_call_logout_and_refresh_follow_the_dialler_guide(self):
        with self.assertRaisesMessage(SmartfloError, "Agent is on a call."):
            SmartfloClient(self.Http(body={"success": False, "message": "Agent is on a call."})).session_call("T", "5")
        c = SmartfloClient(self.Http(body={"success": False, "message": "Agent is not logged in."}))
        self.assertEqual(c.logout("T", "5"), {"success": False, "message": "Agent is not logged in."})     # already out: fine
        with self.assertRaises(SmartfloError):
            SmartfloClient(self.Http(body={"success": False, "message": "Something else"})).logout("T", "5")
        h = self.Http(body={"success": True, "access_token": "T2", "expires_in": 3600})
        self.assertEqual(SmartfloClient(h).refresh("T1"), "T2")
        self.assertEqual((h.sent["method"], h.sent["url"].endswith("/v1/auth/refresh"), h.sent["headers"]["Authorization"]), ("POST", True, "Bearer T1"))
        with self.assertRaises(SmartfloError):
            SmartfloClient(self.Http(body={"success": False, "message": "Invalid token provided"})).refresh("T1")

    @ON
    def test_manual_dial_inside_a_session_uses_the_dialer_type_and_no_caller_id(self):
        h = self.Http(body={"success": True})
        with override_settings(SMARTFLO_CALLER_ID="1600322818"):
            SmartfloClient(h).click_to_call("T", "0606", "9876543210", in_session=True)
        self.assertEqual(h.sent["json"], {"agent_number": "0606", "destination_number": "9876543210", "async": 1, "type": "dialer"})

    @ON
    @override_settings(SMARTFLO_CALLER_ID="1600322818")
    def test_the_outgoing_number_id_is_found_from_the_number_list(self):
        from dialer.setup import SetupError, caller_did_id

        class C:
            def __init__(self, items):
                self.items = items

            def list_my_numbers(self):
                return self.items
        mine = [{"id": "11", "did": "+911234567890", "alias": "1"}, {"id": "546294", "did": "+911600322818", "alias": "2"}]
        self.assertEqual(caller_did_id(C(mine)), "546294")
        with self.assertRaises(SetupError) as cm:
            caller_did_id(C([{"id": "11", "did": "+911234567890"}]))
        self.assertIn("+911234567890 (id 11)", str(cm.exception))
        self.assertIn("set-smartflo.sh did", str(cm.exception))
        with override_settings(SMARTFLO_CALLER_DID_ID="1000631"):
            self.assertEqual(caller_did_id(C([])), "1000631")             # the Admin's own id wins, no lookup
        with override_settings(SMARTFLO_CALLER_ID=""):
            with self.assertRaises(SetupError):
                caller_did_id(C(mine))

    def test_find_id_reads_any_wrapping(self):
        self.assertEqual(find_id({"data": {"items": [{"id": 5, "name": "Alpha"}, {"id": 6, "name": "beta"}]}}, name="BETA"), "6")
        self.assertEqual(find_id([{"id": 1, "code": "x_y"}], code="X_Y"), "1")
        self.assertIsNone(find_id({"data": []}, name="none"))


@ON
@override_settings(SMARTFLO_CALLER_ID="1600322818")
class PerAgentCampaignTests(DialerBase):
    """Campaign.per_agent: every agent gets a Smartflo campaign and lead list of their own, made at their first start, so agents never ring
    each other's customers."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        self.fake = Account()
        p = mock.patch.object(views, "get_client", return_value=self.fake)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch("dialer.views.sleep")
        q.start()
        self.addCleanup(q.stop)
        Campaign.objects.filter(key="dpd1_7").update(per_agent=True, smartflo_campaign_id="", smartflo_list_id="")
        type(self.l1).objects.filter(pk=self.l2.pk).update(dpd=3, assigned_agent=self.a2)

    def calls(self, name):
        return [c for c in self.fake.calls if c[0] == name]

    def test_the_first_start_makes_the_agents_own_list_and_campaign_with_only_that_agent(self):
        r = self.start(self.a1)
        self.assertEqual(r.status_code, 200, r.content)
        from dialer.models import AgentRoute
        route = AgentRoute.objects.get(user=self.a1)
        body = self.calls("create_campaign")[0][1]
        self.assertEqual(body["agent"], [501])                                   # Asha only, not Ravi
        self.assertEqual(body["lead_list_map"], [{"list_id": route.smartflo_list_id, "list_status": "1"}])
        self.assertRegex(body["name"], r"^[A-Za-z0-9 ]+$")
        self.assertEqual(body["name"], route.smartflo_name)
        self.assertEqual(self.calls("session_create")[0][2], route.smartflo_campaign_id)
        self.assertEqual(self.calls("session_call")[0][2], route.smartflo_campaign_id)
        self.assertEqual(self.calls("bulk_add_leads")[0][1], route.smartflo_list_id)    # her customers go to her list

    def test_each_agent_gets_a_different_campaign_and_list(self):
        self.start(self.a1)
        self.start(self.a2)
        from dialer.models import AgentRoute
        a, b = AgentRoute.objects.get(user=self.a1), AgentRoute.objects.get(user=self.a2)
        self.assertNotEqual((a.smartflo_campaign_id, a.smartflo_list_id), (b.smartflo_campaign_id, b.smartflo_list_id))
        self.assertNotEqual(a.smartflo_name, b.smartflo_name)
        self.assertEqual(self.calls("create_campaign")[1][1]["agent"], [502])

    def test_the_second_start_reuses_the_agents_campaign(self):
        self.start(self.a1)
        self.post(self.a1, "session/stop")
        self.start(self.a1)
        self.assertEqual(len(self.calls("create_campaign")), 1)
        self.assertEqual(len(self.calls("create_lead_list")), 1)

    def test_an_existing_campaign_with_that_name_in_smartflo_is_adopted(self):
        from dialer.setup import agent_route_name
        name = agent_route_name(Campaign.objects.get(key="dpd1_7"), self.a1)
        self.fake.lead_lists.append({"id": 777, "name": name})
        self.fake.campaigns.append({"id": 888, "name": name})
        self.start(self.a1)
        from dialer.models import AgentRoute
        self.assertEqual((AgentRoute.objects.get().smartflo_campaign_id, AgentRoute.objects.get().smartflo_list_id), ("888", "777"))
        self.assertEqual(self.calls("create_campaign"), [])

    def test_the_campaign_needs_no_ids_by_hand_but_must_be_switched_on(self):
        Campaign.objects.filter(key="dpd1_7").update(active=False)
        self.assertEqual(self.start(self.a1).status_code, 400)

    def test_old_entries_in_the_agents_own_list_are_cleared_at_the_start(self):
        self.fake.lead_lists.append({"id": 777, "name": __import__("dialer.setup", fromlist=["x"]).agent_route_name(Campaign.objects.get(key="dpd1_7"), self.a1)})
        self.fake.leads = {"data": [{"lead_id": "x1", "field_0": "9000000000", "field_2": "OLD1"}]}
        self.start(self.a1)
        self.assertEqual([c[1] for c in self.calls("delete_lead")], ["x1"])

    def test_stop_and_pause_use_the_agents_own_campaign_and_nothing_is_withdrawn(self):
        self.start(self.a1)
        from dialer.models import AgentRoute
        cid = AgentRoute.objects.get(user=self.a1).smartflo_campaign_id
        self.post(self.a1, "session/pause", {"reason": "lunch"})
        self.post(self.a1, "session/resume")
        self.fake.calls.clear()
        self.post(self.a1, "session/stop")
        self.assertEqual(self.calls("logout")[0][2], cid)
        self.assertEqual(self.calls("delete_leads"), [])            # the agent's list is their own: nothing to take out for a colleague

    def test_an_agent_without_a_smartflo_login_is_told_so(self):
        from dialer.models import DialerProfile
        DialerProfile.objects.filter(user=self.a1).update(smartflo_email="")
        r = self.start(self.a1)
        self.assertEqual(r.status_code, 400)
        self.assertIn("login id", r.json()["detail"])

    def test_a_smartflo_refusal_when_making_the_campaign_is_shown(self):
        self.fake.fail = {"create_campaign": SmartfloError("bad", 422)}
        r = self.start(self.a1)
        self.assertEqual(r.status_code, 400)
        self.assertIn("would not create your campaign", r.json()["detail"])

    def test_dial_rules_reach_every_agents_campaign(self):
        self.start(self.a1)
        self.start(self.a2)
        self.fake.calls.clear()
        r = self.post(self.admin, "smartflo/rules/dpd1_7")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(self.calls("update_campaign")), 2)

    def test_the_head_can_switch_it_on_per_campaign_and_the_row_counts_agent_campaigns(self):
        Campaign.objects.filter(key="dpd1_7").update(per_agent=False)
        r = self.api(self.head).put("/api/dialer/campaigns/dpd1_7", {"per_agent": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["per_agent"], r.json()["agent_campaigns"], r.json()["configured"]), (True, 0, True))
        self.start(self.a1)
        rows = self.api(self.head).get("/api/dialer/pools").json()["pools"]
        self.assertEqual(next(c for c in rows if c["key"] == "dpd1_7")["agent_campaigns"], 1)

    def test_a_campaign_that_is_not_per_agent_still_uses_its_shared_ids(self):
        Campaign.objects.filter(key="dpd1_7").update(per_agent=False, smartflo_campaign_id="111", smartflo_list_id="222")
        self.fake = Account()
        views.get_client.return_value = self.fake
        self.start(self.a1)
        self.assertEqual(self.calls("session_create")[0][2], "111")
        self.assertEqual(self.calls("create_campaign"), [])

    def test_a_session_opened_before_the_switch_was_ticked_still_stops_and_pauses_in_the_shared_campaign(self):
        Campaign.objects.filter(key="dpd1_7").update(per_agent=False, smartflo_campaign_id="111", smartflo_list_id="222")
        self.start(self.a1)
        Campaign.objects.filter(key="dpd1_7").update(per_agent=True)          # the head ticks the box while the agent is signed in
        self.fake.calls.clear()
        self.assertEqual(self.post(self.a1, "session/pause", {"reason": "lunch"}).status_code, 200)
        self.post(self.a1, "session/stop")
        self.assertEqual([c[2] for c in self.calls("session_call")], ["111"])
        self.assertEqual([c[2] for c in self.calls("logout")], ["111"])
