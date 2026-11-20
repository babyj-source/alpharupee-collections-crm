from datetime import timedelta

from django.utils import timezone

from dialer import views
from dialer.models import DialerSession
from dialer.smartflo import SmartfloError
from dialer.tests.test_dialer import DialerBase, FakeSmartflo


class OldSessionFake(FakeSmartflo):
    """Smartflo still has the agent's earlier session: the first session call is refused, the next one (after a logout) works."""

    def __init__(self):
        super().__init__()
        self.refused = False

    def session_call(self, token, campaign_id, start=True):
        if not self.refused:
            self.refused = True
            self.calls.append(("session_call_refused",))
            raise SmartfloError("Smartflo did not start the session call. Agent is on a call.")
        return super().session_call(token, campaign_id, start)


class SessionFlowTests(DialerBase):
    def test_a_leftover_session_is_closed_and_the_start_retried_once(self):
        fake = OldSessionFake()
        self.fake = fake
        views.get_client.return_value = fake
        r = self.start()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(fake.names().count("session_create"), 2)
        self.assertEqual([n for n in fake.names() if n in ("session_call_refused", "logout", "session_call")],
                         ["session_call_refused", "logout", "session_call"])

    def test_a_second_refusal_is_shown_to_the_agent(self):
        self.fake.fail = {"session_call": SmartfloError("Smartflo did not start the session call. Agent is on a call.")}
        r = self.start()
        self.assertEqual(r.status_code, 502)
        self.assertIn("on a call", r.json()["detail"])
        self.assertFalse(DialerSession.objects.exists())

    def test_too_many_login_tries_gets_a_plain_message(self):
        self.fake.fail = {"agent_login": SmartfloError("Smartflo refused the request (429). Too many login attempts have been made for this account.", status=429)}
        r = self.start()
        self.assertEqual(r.status_code, 429)
        self.assertIn("5 minutes", r.json()["detail"])

    def test_token_is_renewed_shortly_before_it_lapses(self):
        self.start()
        s = DialerSession.objects.get()
        old = s.token_enc
        s.token_expires_at = timezone.now() + timedelta(minutes=5)
        s.save()
        d = self.api(self.a1).get("/api/dialer/session").json()
        self.assertTrue(d["active"])
        s.refresh_from_db()
        self.assertNotEqual(s.token_enc, old)
        self.assertGreater(s.token_expires_at, timezone.now() + timedelta(minutes=50))
        self.assertEqual(views.unseal(s.token_enc), "agent-token-2")
        self.assertEqual(self.fake.names().count("refresh"), 1)

    def test_token_with_time_left_is_not_renewed_and_a_failed_renewal_changes_nothing(self):
        self.start()
        self.api(self.a1).get("/api/dialer/session")
        self.assertNotIn("refresh", self.fake.names())
        s = DialerSession.objects.get()
        s.token_expires_at = timezone.now() + timedelta(minutes=5)
        s.save()
        before = s.token_enc
        self.fake.fail = {"refresh": SmartfloError("Invalid token provided")}
        self.assertTrue(self.api(self.a1).get("/api/dialer/session").json()["active"])
        s.refresh_from_db()
        self.assertEqual(s.token_enc, before)

    def test_manual_dial_in_a_session_is_the_dialler_panel_call(self):
        self.start()
        r = self.post(self.a1, "call", {"loan_id": self.l1.pk, "which": "borrower"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(self.fake.in_session)

    def test_manual_dial_without_a_session_is_the_ordinary_call(self):
        r = self.post(self.a1, "call", {"loan_id": self.l1.pk, "which": "borrower"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(self.fake.in_session)

    def test_manual_dial_refusal_in_a_session_says_how_to_make_an_ordinary_call(self):
        self.start()
        self.fake.fail = {"click_to_call": SmartfloError("Smartflo did not place the call. No agent session details were found.")}
        r = self.post(self.a1, "call", {"loan_id": self.l1.pk, "which": "borrower"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("Stop the campaign", r.json()["detail"])


class OtherCampaignFake(FakeSmartflo):
    def session_create(self, token, campaign_id, name):
        super().session_create(token, campaign_id, name)
        return {"success": True, "left": ["Collection Campaign"]}


class OtherCampaignTests(DialerBase):
    def test_the_agent_is_told_they_were_signed_out_of_their_other_campaign(self):
        self.fake = OtherCampaignFake()
        views.get_client.return_value = self.fake
        r = self.start()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["left"], ["Collection Campaign"])
        from accounts.models import AuditLog
        self.assertIn("Collection Campaign", AuditLog.objects.filter(action="dialer_started").latest("id").detail)


class PauseTests(DialerBase):
    def pause(self, user=None, reason="lunch"):
        return self.post(user or self.a1, "session/pause", {"reason": reason})

    def test_pause_stops_the_session_call_and_resume_starts_it_again(self):
        self.assertEqual(self.start().status_code, 200)
        r = self.pause()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["paused"]["label"], "Lunch break")
        self.assertEqual([c for c in self.fake.calls if c[0] == "session_call"][-1][3], False)          # startOrEnd False: no new customers
        s = DialerSession.objects.get()
        self.assertIsNotNone(s.paused_at)
        self.assertEqual(self.api(self.a1).get("/api/dialer/session").json()["paused"]["label"], "Lunch break")
        r = self.post(self.a1, "session/resume")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([c for c in self.fake.calls if c[0] == "session_call"][-1][3], True)
        s.refresh_from_db()
        self.assertIsNone(s.paused_at)
        self.assertEqual(self.api(self.a1).get("/api/dialer/session").json()["paused"], None)
        from dialer.models import DialerPause
        p = DialerPause.objects.get()
        self.assertEqual((p.reason, p.user_id), ("lunch", self.a1.pk))
        self.assertIsNotNone(p.ended_at)
        from accounts.models import AuditLog
        self.assertEqual(list(AuditLog.objects.filter(action__in=("dialer_paused", "dialer_resumed")).order_by("id").values_list("action", "detail")),
                         [("dialer_paused", "Lunch break"), ("dialer_resumed", "Lunch break")])

    def test_a_reason_from_the_list_is_required(self):
        self.start()
        self.assertEqual(self.post(self.a1, "session/pause", {}).status_code, 400)
        self.assertEqual(self.pause(reason="because").status_code, 400)
        self.assertEqual(DialerSession.objects.get().paused_at, None)

    def test_no_pause_while_a_call_is_on_or_its_outcome_is_owed(self):
        self.start()
        DialerSession.objects.update(on_call=True)
        r = self.pause()
        self.assertEqual(r.status_code, 400)
        self.assertIn("Finish the call", r.json()["detail"])
        DialerSession.objects.update(on_call=False, last_unique_id="u1", released=False)
        self.assertEqual(self.pause().status_code, 400)
        self.assertNotIn(False, [c[3] for c in self.fake.calls if c[0] == "session_call"])               # Smartflo was never told

    def test_if_smartflo_refuses_the_pause_nothing_changes(self):
        self.start()
        self.fake.fail = {"session_call": SmartfloError("Smartflo did not start the session call. Not allowed.")}
        r = self.pause()
        self.assertEqual(r.status_code, 502)
        self.assertIsNone(DialerSession.objects.get().paused_at)
        from dialer.models import DialerPause
        self.assertFalse(DialerPause.objects.exists())

    def test_pausing_twice_and_resuming_when_not_paused_are_harmless(self):
        self.start()
        self.assertEqual(self.pause().status_code, 200)
        self.assertEqual(self.pause(reason="tea").status_code, 400)
        self.assertEqual(self.post(self.a1, "session/resume").status_code, 200)
        self.assertEqual(self.post(self.a1, "session/resume").status_code, 200)

    def test_resume_when_smartflo_says_the_session_call_is_already_running_still_resumes(self):
        self.start()
        self.pause()
        self.fake.fail = {"session_call": SmartfloError("Smartflo did not start the session call. Agent is on a call.")}
        self.assertEqual(self.post(self.a1, "session/resume").status_code, 200)
        self.assertIsNone(DialerSession.objects.get().paused_at)

    def test_heads_see_who_is_paused_and_why_and_stopping_closes_the_pause(self):
        self.start()
        self.pause(reason="tech")
        a = next(x for x in self.api(self.head).get("/api/dialer/live").json()["agents"] if x["name"] == "Agent One")
        self.assertEqual((a["state"], a["pause_reason"]), ("paused", "Technical problem (phone, headset, internet)"))
        self.assertEqual(self.post(self.a1, "session/stop").status_code, 200)
        from dialer.models import DialerPause
        self.assertIsNotNone(DialerPause.objects.get().ended_at)

    def test_the_screen_gets_the_list_of_reasons(self):
        reasons = self.api(self.a1).get("/api/dialer/status").json()["pause_reasons"]
        self.assertEqual(reasons[0], {"key": "lunch", "label": "Lunch break"})
        self.assertEqual(reasons[-1]["key"], "other")

    def test_no_session_no_pause(self):
        self.assertEqual(self.pause().status_code, 400)
