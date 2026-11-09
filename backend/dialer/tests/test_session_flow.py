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
