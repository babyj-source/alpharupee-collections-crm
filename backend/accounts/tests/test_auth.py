from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import otp
from accounts.models import AuditLog, OtpChallenge, User

PW = "S3cure-passw0rd!"


class AuthFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("head@example.com", PW, name="Head", mobile="919876543210", role="head")
        self.c = APIClient()
        self.sent = []
        patcher = mock.patch.object(otp, "send_sms", side_effect=lambda m, code: self.sent.append((m, code)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _login(self, email="head@example.com", password=PW):
        return self.c.post("/api/auth/login", {"email": email, "password": password}, format="json")

    def test_password_alone_does_not_log_in(self):
        r = self._login()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["otp_required"])
        self.assertEqual(r.json()["mobile_hint"], "XXXXXXXX3210")
        self.assertEqual(self.c.get("/api/auth/me").status_code, 403)

    def test_full_flow(self):
        self._login()
        code = self.sent[-1][1]
        r = self.c.post("/api/auth/verify", {"code": code}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["role"], "head")
        self.assertEqual(self.c.get("/api/auth/me").json()["user"]["email"], "head@example.com")
        self.c.post("/api/auth/logout", format="json")
        self.assertEqual(self.c.get("/api/auth/me").status_code, 403)

    def test_code_is_not_stored_in_clear(self):
        self._login()
        code = self.sent[-1][1]
        self.assertNotIn(code, OtpChallenge.objects.get().code_hash)

    def test_wrong_password_and_unknown_user_look_the_same(self):
        a = self._login(password="nope")
        b = self._login(email="nobody@example.com")
        self.assertEqual(a.status_code, 400)
        self.assertEqual(a.json(), b.json())
        self.assertEqual(self.sent, [])

    def test_lockout_after_repeated_failures(self):
        for _ in range(5):
            self._login(password="nope")
            cache.clear()
        self.assertEqual(self._login().status_code, 429)  # even the right password is refused
        self.assertTrue(AuditLog.objects.filter(action="account_locked").exists())

    def test_wrong_code_is_refused_and_attempts_are_capped(self):
        self._login()
        good = self.sent[-1][1]
        bad = "000000" if good != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.c.post("/api/auth/verify", {"code": bad}, format="json").status_code, 400)
        cache.clear()
        # the sixth attempt burns the challenge, so even the right code fails now
        self.c.post("/api/auth/verify", {"code": bad}, format="json")
        self.assertEqual(self.c.post("/api/auth/verify", {"code": good}, format="json").status_code, 400)

    def test_code_cannot_be_used_twice(self):
        self._login()
        code = self.sent[-1][1]
        self.assertEqual(self.c.post("/api/auth/verify", {"code": code}, format="json").status_code, 200)
        other = APIClient()
        self.assertEqual(other.post("/api/auth/verify", {"code": code}, format="json").status_code, 400)

    def test_verify_without_login_step(self):
        self.assertEqual(APIClient().post("/api/auth/verify", {"code": "123456"}, format="json").status_code, 400)

    def test_resend_invalidates_old_code(self):
        self._login()
        first = self.sent[-1][1]
        self.c.post("/api/auth/resend", format="json")
        second = self.sent[-1][1]
        if first != second:
            self.assertEqual(self.c.post("/api/auth/verify", {"code": first}, format="json").status_code, 400)
        self.assertEqual(self.c.post("/api/auth/verify", {"code": second}, format="json").status_code, 200)

    @override_settings(OTP_MAX_SENDS_PER_HOUR=2)
    def test_send_quota(self):
        self._login()
        self.c.post("/api/auth/resend", format="json")
        self.assertEqual(self.c.post("/api/auth/resend", format="json").status_code, 429)

    def test_sms_failure_gives_503_and_no_login(self):
        with mock.patch.object(otp, "send_sms", side_effect=otp.OtpDeliveryError("x")):
            self.assertEqual(self._login().status_code, 503)
        self.assertEqual(self.c.get("/api/auth/me").status_code, 403)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self._login().status_code, 400)

    def test_audit_has_no_secrets(self):
        self._login()
        code = self.sent[-1][1]
        self.c.post("/api/auth/verify", {"code": code}, format="json")
        blob = " ".join(f"{a.detail} {a.email_tried} {a.action}" for a in AuditLog.objects.all())
        self.assertNotIn(code, blob)
        self.assertNotIn(PW, blob)
        self.assertNotIn("9876543210", blob)


class Msg91Tests(TestCase):
    @override_settings(OTP_DEV_MODE=False, MSG91_AUTHKEY="", MSG91_OTP_TEMPLATE_ID="")
    def test_unconfigured_raises(self):
        with self.assertRaises(otp.OtpDeliveryError):
            otp.send_sms("919876543210", "123456")

    @override_settings(OTP_DEV_MODE=False, MSG91_AUTHKEY="k", MSG91_OTP_TEMPLATE_ID="tpl", MSG91_API="flow", MSG91_OTP_VARIABLE="OTP")
    def test_flow_payload_and_parsing(self):
        ok = mock.Mock(status_code=200, json=lambda: {"type": "success", "message": "req123"})
        with mock.patch("accounts.otp.requests.post", return_value=ok) as post:
            rid = otp.send_sms("919876543210", "123456")
        kwargs = post.call_args.kwargs
        self.assertEqual(rid, "req123")
        self.assertTrue(kwargs["url"].endswith("/api/v5/flow"))
        self.assertEqual(kwargs["headers"]["authkey"], "k")  # key travels in a header, not in the body
        self.assertEqual(
            kwargs["json"],
            {"template_id": "tpl", "short_url": "0", "recipients": [{"mobiles": "919876543210", "OTP": "123456"}]},
        )
        bad = mock.Mock(status_code=200, json=lambda: {"type": "error", "message": "Template ID Missing or Invalid Template"})
        with mock.patch("accounts.otp.requests.post", return_value=bad):
            with self.assertRaises(otp.OtpDeliveryError) as ctx:
                otp.send_sms("919876543210", "123456")
        self.assertIn("Invalid Template", str(ctx.exception))
        self.assertNotIn("123456", str(ctx.exception))

    @override_settings(OTP_DEV_MODE=False, MSG91_AUTHKEY="k", MSG91_OTP_TEMPLATE_ID="tpl", MSG91_API="otp", MSG91_OTP_VARIABLE="OTP")
    def test_otp_mode_payload(self):
        ok = mock.Mock(status_code=200, json=lambda: {"type": "success", "message": "req9"})
        with mock.patch("accounts.otp.requests.post", return_value=ok) as post:
            otp.send_sms("919876543210", "123456")
        kwargs = post.call_args.kwargs
        self.assertTrue(kwargs["url"].endswith("/api/v5/otp"))
        self.assertNotIn("authkey", kwargs["params"])
        self.assertEqual(kwargs["params"]["otp"], "123456")
        self.assertEqual(kwargs["json"], {"OTP": "123456"})


class ForgotPasswordTests(TestCase):
    NEW = "Brand-new-pass-77"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("head@example.com", PW, name="Head", mobile="919876543210", role="head")
        self.c = APIClient()
        self.sent = []
        patcher = mock.patch.object(otp, "send_sms", side_effect=lambda m, code: self.sent.append((m, code)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def forgot(self, email="head@example.com", client=None):
        return (client or self.c).post("/api/auth/forgot", {"email": email}, format="json")

    def reset(self, code, password=None, client=None):
        return (client or self.c).post("/api/auth/reset", {"code": code, "new_password": password or self.NEW}, format="json")

    def login_ok(self, password):
        c = APIClient()
        r = c.post("/api/auth/login", {"email": "head@example.com", "password": password}, format="json")
        if r.status_code != 200:
            return False
        return c.post("/api/auth/verify", {"code": self.sent[-1][1]}, format="json").status_code == 200

    def test_reset_with_the_sms_code_then_sign_in_with_the_new_password(self):
        r = self.forgot()
        self.assertEqual(r.json(), {"sent": True, "expires_in": 600})
        self.assertEqual(self.sent[-1][0], "919876543210")            # goes to the registered mobile, not to anything the caller typed
        r = self.reset(self.sent[-1][1])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.c.get("/api/auth/me").status_code, 403)   # resetting does not sign anyone in
        self.assertFalse(self.login_ok(PW))                             # the old password is dead
        cache.clear()
        self.assertTrue(self.login_ok(self.NEW))                        # new password + a fresh code works
        self.assertTrue(AuditLog.objects.filter(action="password_reset", user=self.user).exists())

    def test_unknown_inactive_and_real_e_mails_get_the_same_answer(self):
        User.objects.create_user("gone@example.com", PW, name="Gone", mobile="919800000001", role="agent", is_active=False)
        answers = [self.forgot(e) for e in ("head@example.com", "nobody@example.com", "gone@example.com")]
        self.assertEqual({a.status_code for a in answers}, {200})
        self.assertEqual(len({str(a.json()) for a in answers}), 1)
        self.assertEqual(len(self.sent), 1)                              # only the real user got an SMS
        # and the code step cannot tell them apart either
        self.forgot("nobody@example.com")
        self.assertEqual(self.reset("123456").json(), {"detail": "Wrong or expired code."})

    def test_wrong_code_is_refused_and_five_wrong_guesses_burn_the_code(self):
        self.forgot()
        good = self.sent[-1][1]
        for _ in range(5):
            self.assertEqual(self.reset("000000" if good != "000000" else "111111").status_code, 400)
            cache.clear()
        self.assertEqual(self.reset(good).status_code, 400)             # burned even though this one is right
        self.assertTrue(self.login_ok(PW))

    def test_code_works_once_and_expires(self):
        self.forgot()
        code = self.sent[-1][1]
        self.assertEqual(self.reset(code).status_code, 200)
        self.assertEqual(self.reset(code).status_code, 400)             # the session was cleared and the code used
        self.forgot()
        OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.reset(self.sent[-1][1]).status_code, 400)

    def test_weak_password_is_rejected_without_using_up_the_code(self):
        self.forgot()
        code = self.sent[-1][1]
        r = self.reset(code, password="short")
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least 10", r.json()["detail"])
        self.assertEqual(self.reset(code).status_code, 200)             # same code still good

    def test_needs_the_e_mail_step_first_and_it_times_out(self):
        self.assertEqual(self.reset("123456").status_code, 400)          # no session
        self.forgot()
        s = self.c.session
        s["reset_at"] = timezone.now().timestamp() - 16 * 60
        s.save()
        self.assertEqual(self.reset(self.sent[-1][1]).status_code, 400)

    def test_clears_a_lockout_and_the_forced_change_and_ends_open_sessions(self):
        User.objects.filter(pk=self.user.pk).update(failed_logins=3, locked_until=timezone.now() + timedelta(minutes=10), must_change_password=True)
        other = APIClient()
        other.force_login(User.objects.get(pk=self.user.pk))
        self.assertEqual(other.get("/api/auth/me").status_code, 200)
        self.forgot()
        self.assertEqual(self.reset(self.sent[-1][1]).status_code, 200)
        u = User.objects.get(pk=self.user.pk)
        self.assertEqual((u.failed_logins, u.locked_until, u.must_change_password), (0, None, False))
        self.assertEqual(other.get("/api/auth/me").status_code, 403)    # a session opened with the old password is gone
        cache.clear()
        self.assertTrue(self.login_ok(self.NEW))

    def test_sms_trouble_does_not_reveal_anything_and_is_recorded(self):
        with mock.patch.object(otp, "send_sms", side_effect=otp.OtpDeliveryError("MSG91 refused (HTTP 401): no auth")):
            r = self.forgot()
        self.assertEqual(r.json(), {"sent": True, "expires_in": 600})
        self.assertTrue(AuditLog.objects.filter(action="otp_send_failed").exists())
        for _ in range(5):
            self.forgot()
            cache.clear()
        self.forgot()                                                     # over the hourly limit: same answer, no more SMS
        self.assertEqual(len(self.sent), 5)
        self.assertTrue(AuditLog.objects.filter(action="reset_blocked").exists())

    def test_nothing_secret_in_the_audit_log(self):
        self.forgot()
        code = self.sent[-1][1]
        self.reset(code)
        dump = str(list(AuditLog.objects.values()))
        for secret in (code, self.NEW, "919876543210"):
            self.assertNotIn(secret, dump)

    def test_throttled(self):
        for _ in range(10):
            self.forgot("x@example.com")
        self.assertEqual(self.forgot("x@example.com").status_code, 429)
