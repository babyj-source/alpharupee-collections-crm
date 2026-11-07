from unittest import mock

from accounts.models import AuditLog
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


class CallLogTests(DialerBase):
    def use(self, fake):
        p = mock.patch.object(views, "get_client", return_value=fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def test_rows_are_matched_to_accounts_and_never_show_numbers(self):
        self.use(Fake())
        r = self.api(self.head).get("/api/dialer/calls?days=7")
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()["results"]
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["account_id"], rows[0]["to"], rows[0]["has_recording"], rows[0]["agent"]), ("L1", "xxxxxx1111", True, "Asha K"))
        self.assertEqual((rows[1]["account_id"], rows[1]["has_recording"]), ("", False))
        self.assertNotIn("919111111111", r.content.decode())
        self.assertNotIn("token=t", r.content.decode())               # recording links are never in the list

    def test_filters_are_passed_on(self):
        f = self.use(Fake())
        self.api(self.head).get("/api/dialer/calls?status=missed&direction=outbound&page=2")
        self.assertEqual((f.calls[0]["call_type"], f.calls[0]["direction"], f.calls[0]["page"]), ("m", "outbound", 2))
        rows = self.api(self.head).get("/api/dialer/calls?agent=ravi").json()["results"]
        self.assertEqual([x["agent"] for x in rows], ["Ravi"])

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

    def test_recording_link_is_one_call_at_a_time_and_audited(self):
        self.use(Fake())
        r = self.api(self.head).post("/api/dialer/calls/recording", {"call_id": "c-1"}, format="json")
        self.assertEqual((r.status_code, r.json()["url"].startswith("https://cloudphone.example/")), (200, True))
        self.assertTrue(AuditLog.objects.filter(action="recording_played", object_id="c-1").exists())
        self.assertEqual(self.api(self.head).post("/api/dialer/calls/recording", {"call_id": "c-2"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.head).post("/api/dialer/calls/recording", {}, format="json").status_code, 400)
        self.assertEqual(self.api(self.a1).post("/api/dialer/calls/recording", {"call_id": "c-1"}, format="json").status_code, 403)

    def test_http_recording_link_is_refused(self):
        bad = {"results": [{"call_id": "c-9", "recording_url": "http://x/y"}]}
        self.use(Fake(bad))
        self.assertEqual(self.api(self.head).post("/api/dialer/calls/recording", {"call_id": "c-9"}, format="json").status_code, 404)

    def test_parse_is_defensive(self):
        self.assertEqual(calllog.parse_records({"nothing": 1}), (0, []))
        self.assertEqual(calllog.parse_records([{"call_id": "z", "client_number": "07827891601"}])[1][0]["number"], "7827891601")
