from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import AuditLog
from loans.models import Activity
from payments import lms
from payments.models import PaymentLink
from payments.service import LinkRefused, post_pending, post_to_lms
from payments.tests.test_payments import P, Resp, URL

CFG = dict(LMS_POST_ENABLED=True, LMS_POST_URL="https://lms.test/post", LMS_POST_USER="crm", LMS_POST_PASSWORD="pw-secret",
           LMS_POST_USER_ID="", LMS_POST_ACCOUNT_CODE="BANK001", LMS_POST_INSTRUMENT="ONLINE", LMS_POST_OK_WORDS="success,ok")


def took(status="SUCCESS"):
    return Resp({"transactions": [{"status": status, "responseCode": None, "response": None}], "status": None})


@override_settings(**CFG)
class PostTests(P):
    def setUp(self):
        super().setUp()
        self.link = PaymentLink.objects.create(loan=self.l2, merchant_txn="ARCTEST1", amount=Decimal("8980"), payment_url=URL, state="paid",
                                               lms_state="pending", paid_at=timezone.now(), created_by=self.a2, easebuzz_txn_id="S2611TEST1")
        self.admin = type(self.head).objects.create_user("ad@x.com", "pw", name="Ad", mobile="919000000009", role="admin")

    def fresh(self):
        self.link.refresh_from_db()
        return self.link

    def test_the_request_is_the_one_sensei_sent_and_carries_the_password_only_as_basic_auth(self):
        with mock.patch("payments.lms.requests.post", return_value=took()) as post:
            post_to_lms(self.link)
        self.assertEqual(post.call_args.args[0], "https://lms.test/post")
        self.assertEqual(post.call_args.kwargs["auth"], ("crm", "pw-secret"))
        body = post.call_args.kwargs["json"]
        self.assertNotIn("pw-secret", str(body))
        t = body["transactions"][0]
        self.assertEqual((t["accountId"], t["transactionName"], t["amount1"], t["sequenceNum"], t["instrument"], t["currencyCode"]),
                         (self.l2.account_id, "Repayment", "8980", -1, "ONLINE", "INR"))
        self.assertEqual(t["reference"], "accountCode:BANK001|instrumentRef:ARCTEST1")
        self.assertEqual(t["transactionId"], "S2611TEST1")                              # Easebuzz's own payment id
        self.assertEqual((t["valueDateStr"], t["userId"]), (timezone.localdate().isoformat(), "crm"))
        self.assertEqual((body["safeMode"], body["actionType"], body["transactionFormat"], body["commitSize"]), (False, "EXECUTION", "Default", 1))

    def test_a_repayment_the_lms_took_is_marked_posted_with_a_timeline_entry(self):
        with mock.patch("payments.lms.requests.post", return_value=took()):
            post_to_lms(self.link)
        l = self.fresh()
        self.assertEqual((l.lms_state, l.lms_tries), ("posted", 1))
        self.assertIsNotNone(l.lms_posted_at)
        self.assertTrue(Activity.objects.filter(loan=self.l2, detail__startswith="Repayment of ₹8,980 posted to the LMS").exists())
        self.assertTrue(AuditLog.objects.filter(action="lms_repayment_posted").exists())

    def test_a_posted_repayment_is_never_sent_again(self):
        with mock.patch("payments.lms.requests.post", return_value=took()) as post:
            post_to_lms(self.link)
            post_to_lms(self.link)
            post_to_lms(self.link, again=True)
            post_pending()
        self.assertEqual(post.call_count, 1)

    def test_a_refusal_fails_it_and_is_not_retried_by_the_timer(self):
        with mock.patch("payments.lms.requests.post", return_value=Resp({"transactions": [{"status": "FAILED", "response": "Account closed"}]})) as post:
            post_to_lms(self.link)
            post_pending()
            post_pending()
        l = self.fresh()
        self.assertEqual((l.lms_state, post.call_count), ("failed", 1))
        self.assertIn("rejected", l.lms_note)

    def test_wrong_user_or_password_fails_it_with_a_plain_note(self):
        with mock.patch("payments.lms.requests.post", return_value=Resp({}, 401)):
            post_to_lms(self.link)
        self.assertEqual((self.fresh().lms_state, "user or password" in self.fresh().lms_note), ("failed", True))

    def test_an_answer_we_do_not_recognise_is_not_called_posted(self):
        for reply in (Resp({"hello": "world"}), Resp({"transactions": [{"status": "MAYBE"}]}), Resp(None, 200), Resp({}, 500)):
            PaymentLink.objects.filter(pk=self.link.pk).update(lms_state="pending")
            with mock.patch("payments.lms.requests.post", return_value=reply):
                post_to_lms(self.link)
            self.assertEqual(self.fresh().lms_state, "failed", reply._b)

    def test_a_timeout_may_have_reached_the_lms_so_it_fails_and_waits_for_a_person(self):
        with mock.patch("payments.lms.requests.post", side_effect=requests.ReadTimeout()) as post:
            post_to_lms(self.link)
            post_pending()
        self.assertEqual((self.fresh().lms_state, post.call_count), ("failed", 1))
        self.assertIn("may or may not", self.fresh().lms_note)

    def test_when_the_lms_cannot_be_reached_it_is_tried_again_each_minute_then_given_up(self):
        err = requests.ConnectionError("Max retries exceeded: Failed to resolve")
        with mock.patch("payments.lms.requests.post", side_effect=err) as post:
            for _ in range(8):
                post_pending()
        l = self.fresh()
        self.assertEqual((l.lms_state, post.call_count, l.lms_tries), ("failed", 5, 5))

    def test_it_is_posted_by_the_timer_once_the_lms_is_reachable_again(self):
        with mock.patch("payments.lms.requests.post", side_effect=[requests.ConnectionError("Failed to resolve"), took()]) as post:
            self.assertEqual(post_pending(), (0, 0, 1))
            self.assertEqual(post_pending(), (1, 0, 0))
        self.assertEqual((self.fresh().lms_state, post.call_count), ("posted", 2))

    @override_settings(LMS_POST_ENABLED=False)
    def test_nothing_is_sent_while_posting_is_switched_off(self):
        with mock.patch("payments.lms.requests.post") as post:
            self.assertEqual(post_pending()[2], 1)
            with self.assertRaises(LinkRefused):
                post_to_lms(self.link)
        self.assertFalse(post.called)
        self.assertEqual(self.fresh().lms_state, "pending")

    @override_settings(LMS_POST_ACCOUNT_CODE="")
    def test_a_missing_setting_stops_posting_and_says_which(self):
        self.assertIn("bank account code", lms.missing())
        with mock.patch("payments.lms.requests.post") as post:
            with self.assertRaises(LinkRefused):
                post_to_lms(self.link)
        self.assertFalse(post.called)

    def test_a_link_stuck_in_posting_is_failed_after_ten_minutes_and_not_resent(self):
        PaymentLink.objects.filter(pk=self.link.pk).update(lms_state="posting", lms_tried_at=timezone.now() - timedelta(minutes=11))
        with mock.patch("payments.lms.requests.post") as post:
            post_pending()
        self.assertEqual(self.fresh().lms_state, "failed")
        self.assertIn("Check the LMS", self.fresh().lms_note)
        self.assertFalse(post.called)

    def test_a_link_that_is_not_paid_is_never_posted(self):
        PaymentLink.objects.filter(pk=self.link.pk).update(state="active", lms_state="")
        self.link.refresh_from_db()
        with mock.patch("payments.lms.requests.post") as post:
            with self.assertRaises(LinkRefused):
                post_to_lms(self.link)
            post_pending()
        self.assertFalse(post.called)

    def test_only_an_admin_can_post_a_failed_one_again(self):
        PaymentLink.objects.filter(pk=self.link.pk).update(lms_state="failed", lms_note="x")
        url = f"/api/payments/link/{self.link.pk}/post-lms"
        with mock.patch("payments.lms.requests.post", return_value=took()) as post:
            self.assertEqual(self.api(self.a2).post(url).status_code, 403)
            self.assertEqual(self.api(self.head).post(url).status_code, 403)
            self.assertFalse(post.called)
            r = self.api(self.admin).post(url)
        self.assertEqual((r.status_code, r.json()["lms"]), (200, "posted"))
        self.assertEqual(self.api(self.admin).post(url).status_code, 400)            # already posted: nothing more to do
        self.assertEqual(post.call_count, 1)

    def test_the_lms_answer_is_shown_to_the_head_not_to_agents(self):
        PaymentLink.objects.filter(pk=self.link.pk).update(lms_state="failed", lms_note="The LMS rejected the repayment.")
        head = self.api(self.head).get("/api/payments/links").json()["results"][0]
        agent = self.api(self.a2).get("/api/payments/links").json()["results"][0]
        self.assertEqual(head["lms_note"], "The LMS rejected the repayment.")
        self.assertNotIn("lms_note", agent)

    def test_the_command_shows_the_request_without_sending_and_reports_status(self):
        out = StringIO()
        with mock.patch("payments.lms.requests.post") as post:
            call_command("post_lms_payments", link="ARCTEST1", dry_run=True, stdout=out)
            call_command("post_lms_payments", "--status", stdout=out)
        self.assertFalse(post.called)
        self.assertIn('"amount1": "8980"', out.getvalue())
        self.assertNotIn("pw-secret", out.getvalue())
        self.assertIn("pending: 1", out.getvalue())

    def test_a_link_the_database_says_is_not_paid_is_not_claimed_even_with_a_stale_copy(self):
        stale = PaymentLink.objects.get(pk=self.link.pk)                              # still says paid
        PaymentLink.objects.filter(pk=self.link.pk).update(state="active", lms_state="")
        with mock.patch("payments.lms.requests.post") as post:
            post_to_lms(stale)
        self.assertFalse(post.called)

    def test_an_answer_that_is_a_list_of_transactions_is_read_and_kept_whole(self):
        reply = Resp([{"accountId": "A1", "status": "SUCCESS", "responseCode": "0", "response": None}])
        with mock.patch("payments.lms.requests.post", return_value=reply):
            post_to_lms(self.link)
        l = self.fresh()
        self.assertEqual(l.lms_state, "posted")
        self.assertTrue(l.lms_reply.startswith("HTTP 200 ["))
        self.assertIn("SUCCESS", l.lms_reply)
        self.assertNotIn("pw-secret", l.lms_reply)

    def test_the_real_success_wording_in_the_response_text_is_read_as_posted(self):
        reply = Resp([{"accountId": "A1", "status": None, "responseCode": None, "response": "S261118076CKPL,SUCCESS,Closed,Loans:MB06"}])
        with mock.patch("payments.lms.requests.post", return_value=reply):
            post_to_lms(self.link)
        l = self.fresh()
        self.assertEqual(l.lms_state, "posted")
        self.assertIn("SUCCESS", l.lms_reply)

    def test_a_failure_in_the_response_text_is_still_a_refusal(self):
        reply = Resp([{"accountId": "A1", "status": None, "responseCode": None, "response": "S261118076CKPL,FAILED,Open,Loans:MB06"}])
        with mock.patch("payments.lms.requests.post", return_value=reply):
            post_to_lms(self.link)
        self.assertEqual(self.fresh().lms_state, "failed")

    def test_a_list_answer_without_a_plain_success_is_unclear_and_kept_whole(self):
        reply = Resp([{"accountId": "A1", "status": None, "responseCode": None, "response": None}])
        with mock.patch("payments.lms.requests.post", return_value=reply):
            post_to_lms(self.link)
        l = self.fresh()
        self.assertEqual(l.lms_state, "failed")
        self.assertIn('"accountId": "A1"', l.lms_reply)

    def test_a_list_answer_that_says_failed_is_a_refusal(self):
        with mock.patch("payments.lms.requests.post", return_value=Resp([{"status": "FAILED", "response": "Value date not allowed"}])):
            post_to_lms(self.link)
        self.assertIn("rejected", self.fresh().lms_note)

    def test_without_easebuzzs_id_nothing_is_posted_and_it_waits(self):
        PaymentLink.objects.filter(pk=self.link.pk).update(easebuzz_txn_id="")
        self.link.refresh_from_db()
        with mock.patch("payments.service.easebuzz.get_link", return_value={"payment_made": 1}), mock.patch("payments.lms.requests.post") as post:
            post_to_lms(self.link)
            post_pending()
        self.assertFalse(post.called)
        l = self.fresh()
        self.assertEqual(l.lms_state, "pending")
        self.assertIn("Easebuzz's transaction id", l.lms_note)

    def test_the_id_is_fetched_from_easebuzz_once_and_then_sent_to_the_lms(self):
        PaymentLink.objects.filter(pk=self.link.pk).update(easebuzz_txn_id="")
        self.link.refresh_from_db()
        with mock.patch("payments.service.easebuzz.get_link", return_value={"payment_made": 1, "transaction_id": "S261109076BUKE"}) as get, \
                mock.patch("payments.lms.requests.post", return_value=took()) as post:
            post_to_lms(self.link)
        self.assertEqual(post.call_args.kwargs["json"]["transactions"][0]["transactionId"], "S261109076BUKE")
        l = self.fresh()
        self.assertEqual((l.lms_state, l.easebuzz_txn_id, get.call_count), ("posted", "S261109076BUKE", 1))

    def test_noticing_a_payment_stores_easebuzzs_transaction_id(self):
        from payments.service import refresh
        PaymentLink.objects.filter(pk=self.link.pk).update(state="active", lms_state="", easebuzz_txn_id="")
        self.link.refresh_from_db()
        data = {"payment_made": 1, "state": "active", "quick_pay_transaction_date": "2026-11-09T11:00:14Z", "transaction_id": "S261109076BUKE"}
        with mock.patch("payments.easebuzz.get_link", return_value=data):
            refresh(self.link)
        l = self.fresh()
        self.assertEqual((l.state, l.lms_state, l.easebuzz_txn_id), ("paid", "pending", "S261109076BUKE"))

    def test_the_minute_job_runs_the_way_the_timer_runs_it_and_posts_the_waiting_repayment(self):
        """The timer starts `manage.py check_payment_links`, which (unlike call_command) runs Django's own start-up checks first.
        A helper named `check` once broke the job there while every other test passed."""
        from payments import easebuzz
        out = StringIO()
        with override_settings(EASEBUZZ_ENABLED=True, EASEBUZZ_KEY="K", EASEBUZZ_SALT="S"), mock.patch("payments.lms.requests.post", return_value=took()) as post:
            call_command("check_payment_links", skip_checks=False, stdout=out)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.fresh().lms_state, "posted")
        self.assertIn("LMS: posted 1", out.getvalue())

    def test_no_management_command_hides_a_django_method(self):
        """Names like check/execute/run_from_argv belong to Django's BaseCommand; reusing one by accident breaks the command at start-up."""
        import importlib
        import pkgutil
        from django.core.management.base import BaseCommand
        from django.apps import apps
        taken = {n for n in dir(BaseCommand) if not n.startswith("_")} - {"handle", "add_arguments", "help"}
        for cfg in apps.get_app_configs():
            if cfg.name.startswith("django."):
                continue
            try:
                pkg = importlib.import_module(cfg.name + ".management.commands")
            except ImportError:
                continue
            for mod in pkgutil.iter_modules(pkg.__path__):
                cls = importlib.import_module(f"{cfg.name}.management.commands.{mod.name}").Command
                own = {n for n, v in vars(cls).items() if not n.startswith("_") and callable(v)}   # methods only: settings like requires_system_checks are meant to be set
                self.assertEqual(own & taken, set(), f"{cfg.name}.{mod.name} redefines {own & taken}")
