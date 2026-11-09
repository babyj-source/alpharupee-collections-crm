from unittest import mock

from datetime import datetime

from django.utils import timezone

from accounts.models import AuditLog
from loans.models import Activity
from dialer import calllog, views
from dialer.smartflo import SmartfloError
from dialer.tests.test_dialer import ON, DialerBase
from django.test import override_settings

REC = {"count": 2, "limit": 50, "page": 1, "results": [
    {"call_id": "c-1", "direction": "outbound", "status": "answered", "date": "2026-11-07", "time": "11:52:14", "call_duration": 48,
     "answered_seconds": 31, "agent_name": "Asha K", "client_number": "919111111111", "recording_url": "https://cloudphone.example/rec?callId=c-1&token=t",
     "dialer_call_details": {"disposition_name": "Undisposed", "campaign_name": "CRM 1–7"}},
    {"call_id": "c-2", "direction": "outbound", "status": "missed", "date": "2026-11-07", "time": "12:00:00", "call_duration": 20, "answered_seconds": 0,
     "agent_name": "Ravi", "client_number": "919000099999", "recording_url": None},
    "junk"]}


class Fake:
    def __init__(self, payload=REC, fail=None):
        self.payload, self.fail, self.calls = payload, fail, []

    def call_records(self, from_date, to_date, page=1, limit=50, call_id=None, call_type=None, direction=None):
        self.calls.append(dict(page=page, limit=limit, call_id=call_id, call_type=call_type, direction=direction, from_date=from_date))
        if self.fail:
            raise self.fail
        if call_id:
            return {"results": [r for r in self.payload["results"] if isinstance(r, dict) and r["call_id"] == call_id]}
        return self.payload


def stamp(a, date_time):
    """Put an activity at a given local time (created_at is automatic)."""
    Activity.objects.filter(pk=a.pk).update(created_at=timezone.make_aware(datetime.strptime(date_time, "%Y-%m-%d %H:%M:%S")))


class CallLogTests(DialerBase):
    def use(self, fake):
        p = mock.patch.object(views, "get_client", return_value=fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def crm_call(self, when="2026-11-07 11:53:00", kind="disposition", loan=None, user=None):
        a = Activity.objects.create(loan=loan or self.l1, user=user or self.a1, kind=kind, disposition="no_answer" if kind == "disposition" else "")
        stamp(a, when)
        return a

    def test_only_calls_placed_or_taken_in_the_crm_are_listed(self):
        self.use(Fake())
        self.crm_call()                                   # the agent logged an outcome 1 minute after c-1 ended
        r = self.api(self.head).get("/api/dialer/calls?days=30")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual([x["call_id"] for x in d["results"]], ["c-1"])      # c-2 is not on any CRM account; nothing else is shown
        self.assertEqual(d["checked"], 2)
        row = d["results"][0]
        self.assertEqual((row["account_id"], row["to"], row["has_recording"], row["agent"], row["crm_user"], row["how"]),
                         ("L1", "xxxxxx1111", True, "Asha K", "Agent One", "outcome logged in the CRM"))
        self.assertNotIn("919111111111", r.content.decode())
        self.assertNotIn("token=t", r.content.decode())               # recording links are never in the list

    def test_a_call_placed_from_the_crm_counts_even_without_an_outcome(self):
        self.use(Fake())
        self.crm_call(when="2026-11-07 11:51:30", kind="call")        # the click, just before Smartflo's record starts
        rows = self.api(self.head).get("/api/dialer/calls?days=30").json()["results"]
        self.assertEqual((rows[0]["call_id"], rows[0]["how"]), ("c-1", "placed from the CRM"))

    def test_calls_with_no_crm_trace_or_far_from_it_are_left_out(self):
        self.use(Fake())
        self.assertEqual(self.api(self.head).get("/api/dialer/calls?days=30").json()["results"], [])
        self.crm_call(when="2026-11-07 14:30:00")                     # hours later: a different call
        self.crm_call(when="2026-11-07 09:00:00", kind="call")        # hours earlier
        self.assertEqual(self.api(self.head).get("/api/dialer/calls?days=30").json()["results"], [])
        self.crm_call(when="2026-11-07 11:53:00", loan=self.l2)       # an outcome on an account that is not on this number
        self.assertEqual(self.api(self.head).get("/api/dialer/calls?days=30").json()["results"], [])

    def test_a_shared_number_shows_the_account_that_was_worked(self):
        self.use(Fake())
        self.crm_call(loan=self.l3)                                   # L3 shares L1's number in the test data
        row = self.api(self.head).get("/api/dialer/calls?days=30").json()["results"][0]
        self.assertEqual(row["account_id"], "L3")

    def test_filters_are_passed_on(self):
        f = self.use(Fake())
        self.api(self.head).get("/api/dialer/calls?status=missed&direction=outbound&page=2")
        self.assertEqual((f.calls[0]["call_type"], f.calls[0]["direction"], f.calls[0]["page"]), ("m", "outbound", 2))
        self.crm_call()
        self.assertEqual(len(self.api(self.head).get("/api/dialer/calls?days=30&agent=agent one").json()["results"]), 1)   # by the CRM user too
        self.assertEqual(self.api(self.head).get("/api/dialer/calls?days=30&agent=nobody").json()["results"], [])

    def test_head_only_and_needs_smartflo_on(self):
        self.use(Fake())
        self.assertEqual(self.api(self.a1).get("/api/dialer/calls").status_code, 403)
        with override_settings(SMARTFLO_ENABLED=False):
            self.assertEqual(self.api(self.head).get("/api/dialer/calls").status_code, 400)

    def test_smartflo_error_is_shown(self):
        self.use(Fake(fail=SmartfloError("Smartflo refused the request (500). boom", status=500)))
        r = self.api(self.head).get("/api/dialer/calls")
        self.assertEqual(r.status_code, 502)
        self.assertIn("boom", r.json()["detail"])

    def test_recording_link_is_for_crm_calls_only_one_at_a_time_and_audited(self):
        self.use(Fake())
        self.crm_call()
        r = self.api(self.head).post("/api/dialer/calls/recording", {"call_id": "c-1"}, format="json")
        self.assertEqual((r.status_code, r.json()["url"].startswith("https://cloudphone.example/")), (200, True))
        self.assertTrue(AuditLog.objects.filter(action="recording_played", object_id="c-1").exists())
        self.assertEqual(self.api(self.head).post("/api/dialer/calls/recording", {"call_id": "c-2"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.head).post("/api/dialer/calls/recording", {}, format="json").status_code, 400)
        self.assertEqual(self.api(self.a1).post("/api/dialer/calls/recording", {"call_id": "c-1"}, format="json").status_code, 403)

    def test_a_recording_of_a_call_outside_the_crm_is_refused(self):
        self.use(Fake())
        r = self.api(self.head).post("/api/dialer/calls/recording", {"call_id": "c-1"}, format="json")      # no CRM activity for it
        self.assertEqual(r.status_code, 404)
        self.assertIn("not placed or taken in the CRM", r.json()["detail"])
        self.assertFalse(AuditLog.objects.filter(action="recording_played").exists())

    def test_http_recording_link_is_refused(self):
        bad = {"results": [{"call_id": "c-9", "recording_url": "http://x/y", "date": "2026-11-07", "time": "11:52:14", "client_number": "919111111111"}]}
        self.use(Fake(bad))
        self.crm_call()
        self.assertEqual(self.api(self.head).post("/api/dialer/calls/recording", {"call_id": "c-9"}, format="json").status_code, 404)

    def test_parse_is_defensive(self):
        self.assertEqual(calllog.parse_records({"nothing": 1}), (0, []))
        self.assertEqual(calllog.parse_records([{"call_id": "z", "client_number": "07827891601"}])[1][0]["number"], "7827891601")
