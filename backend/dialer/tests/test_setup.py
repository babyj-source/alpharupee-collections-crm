from unittest import mock

from django.test import override_settings

from dialer import views
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

    def test_campaign_needs_the_outcomes_first(self):
        r = self.post(self.admin, "smartflo/campaign/dpd8_15")
        self.assertEqual(r.status_code, 400)
        self.assertIn("outcomes", r.json()["detail"])

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
        self.assertEqual((body["number_of_retry"], body["retry_after_minutes"], body["ring_timeout"]), ("3", "120", "30"))
        self.assertTrue(r.json()["campaign"]["configured"])

    def test_existing_items_in_smartflo_are_adopted_not_duplicated(self):
        self.post(self.admin, "smartflo/outcomes")
        self.fake.lead_lists.append({"id": 777, "name": "CRM 8–15 days late"})
        label = Campaign.objects.get(key="dpd8_15").label
        self.fake.lead_lists[0]["name"] = f"CRM {label}"
        self.fake.campaigns.append({"id": 888, "name": f"CRM {label}"})
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
        self.assertEqual((h.sent["method"], h.sent["url"].endswith("/v1/auth/refresh"), h.sent["headers"]["Authorization"]), ("POST", True, "T1"))
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
