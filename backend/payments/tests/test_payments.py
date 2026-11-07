import hashlib
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import AuditLog
from loans.models import Activity, Loan
from loans.tests.test_workflow import Base
from messaging import msg91
from messaging.models import MessageLog
from messaging.service import SendRefused, preview_sms, send_sms_for_loan
from payments import easebuzz
from payments.models import PaymentLink
from payments.service import LinkRefused, StaleClosure, create_for_loan, refresh

CFG = dict(EASEBUZZ_ENABLED=True, EASEBUZZ_KEY="KEY1", EASEBUZZ_SALT="SALT1", EASEBUZZ_SUB_MERCHANT_ID="SUB1",
           EASEBUZZ_BASE_URL="https://testdashboard.easebuzz.in", PAYMENT_LINK_VALID_DAYS=2)
URL = "https://pay.easebuzz.in/quick_pay/abc123"


class Resp:
    def __init__(self, body, status=200):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def created(url=URL):
    return Resp({"status": True, "data": {"payment_url": url, "state": "active"}, "message": "Link created successfully"})


def got(paid=0, state="active", when=None):
    return Resp({"status": True, "data": {"payment_made": paid, "state": state, "quick_pay_transaction_date": when}})


def sha(*p):
    return hashlib.sha512("|".join(p).encode()).hexdigest()


class P(Base):
    def setUp(self):
        super().setUp()
        Loan.objects.filter(pk=self.l2.pk).update(closure_amount=Decimal("8980"), closure_as_on=timezone.localdate(), borrower_name="NEHA SINGH", dpd=45)
        self.l2.refresh_from_db()


@override_settings(**CFG)
class CreateTests(P):
    def test_sends_the_documented_request_and_stores_the_link(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()) as post:
            link, made = create_for_loan(self.l2, self.a2)
        self.assertTrue(made)
        self.assertEqual(post.call_args.args[0], "https://testdashboard.easebuzz.in/easycollect/v1/create")
        b = post.call_args.kwargs["json"]
        self.assertEqual((b["key"], b["name"], b["amount"], b["udf1"], b["udf2"], b["sub_merchant_id"]), ("KEY1", "NEHA SINGH", "8980.00", "CRM", "L2", "SUB1"))
        self.assertEqual(len(b["phone"]), 10)
        self.assertEqual(b["expiry_date"], (timezone.localdate() + timedelta(days=2)).strftime("%d-%m-%Y"))
        expect = sha("KEY1", b["merchant_txn"], b["name"], b["email"], b["phone"], b["amount"], "CRM", "L2", "", "", "", b["message"], "SALT1")
        self.assertEqual(b["hash"], expect)                      # hash order: key|merchant_txn|name|email|phone|amount|udf1..5|message|salt
        self.assertNotIn("SALT1", str(b))                        # the salt is never sent
        self.assertEqual((link.payment_url, link.amount, link.state, link.created_by), (URL, Decimal("8980"), "active", self.a2))
        self.assertTrue(Activity.objects.filter(loan=self.l2, kind="message", detail__startswith="Payment link created").exists())

    @override_settings(EASEBUZZ_SUB_MERCHANT_ID="")
    def test_an_ordinary_merchant_sends_no_sub_merchant_id(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()) as post:
            link, made = create_for_loan(self.l2, self.a2)
        self.assertTrue(made)
        self.assertNotIn("sub_merchant_id", post.call_args.kwargs["json"])
        self.assertTrue(easebuzz.enabled())

    def test_same_day_same_amount_reuses_the_link(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()) as post:
            a, _ = create_for_loan(self.l2, self.a2)
            b, made = create_for_loan(self.l2, self.a2)
        self.assertEqual((a.pk, made, post.call_count), (b.pk, False, 1))

    def test_a_changed_closure_amount_makes_a_new_link(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            create_for_loan(self.l2, self.a2)
            Loan.objects.filter(pk=self.l2.pk).update(closure_amount=Decimal("9000"))
            self.l2.refresh_from_db()
            link, made = create_for_loan(self.l2, self.a2)
        self.assertTrue(made)
        self.assertEqual(link.amount, Decimal("9000"))

    def test_stale_closure_needs_confirmation(self):
        Loan.objects.filter(pk=self.l2.pk).update(closure_as_on=timezone.localdate() - timedelta(days=3))
        self.l2.refresh_from_db()
        with mock.patch("payments.easebuzz.requests.post", return_value=created()) as post:
            with self.assertRaises(StaleClosure):
                create_for_loan(self.l2, self.a2)
            self.assertEqual(post.call_count, 0)
            link, made = create_for_loan(self.l2, self.a2, confirm_stale=True)
        self.assertTrue(made)

    def test_refusals(self):
        for field, value, text in (("closure_amount", Decimal("0"), "zero"), ("crm_status", "paid", "paid or in dispute"),
                                   ("operational_status", "2", "closed"), ("borrower_mobile", "", "valid mobile")):
            Loan.objects.filter(pk=self.l2.pk).update(**{field: value})
            self.l2.refresh_from_db()
            with mock.patch("payments.easebuzz.requests.post") as post:
                with self.assertRaisesRegex(LinkRefused, text):
                    create_for_loan(self.l2, self.a2)
                post.assert_not_called()
            Loan.objects.filter(pk=self.l2.pk).update(closure_amount=Decimal("8980"), crm_status="new", operational_status="0", borrower_mobile="919222222222")

    def test_easebuzz_refusal_and_bad_answers_make_no_link(self):
        for resp in (Resp({"status": False, "message": "Invalid hash"}, 200), Resp({"status": True, "data": {}}), Resp({}, 500)):
            with mock.patch("payments.easebuzz.requests.post", return_value=resp):
                with self.assertRaises(LinkRefused):
                    create_for_loan(self.l2, self.a2)
        self.assertEqual(PaymentLink.objects.count(), 0)

    def test_the_refusal_text_carries_easebuzzs_reason_but_never_the_salt(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=Resp({"status": False, "message": "Invalid hash", "errors": {"sub_merchant_id": ["not found"]}}, 400)):
            with self.assertRaises(LinkRefused) as cm:
                create_for_loan(self.l2, self.a2)
        self.assertIn("HTTP 400", str(cm.exception))
        self.assertIn("Invalid hash", str(cm.exception))
        self.assertIn("sub_merchant_id", str(cm.exception))
        self.assertNotIn("SALT1", str(cm.exception))

    def test_network_failure_is_a_clean_refusal(self):
        import requests
        with mock.patch("payments.easebuzz.requests.post", side_effect=requests.ConnectionError("boom SALT1")):
            with self.assertRaisesRegex(LinkRefused, "could not be reached"):
                create_for_loan(self.l2, self.a2)

    @override_settings(EASEBUZZ_SALT="")
    def test_not_configured(self):
        with self.assertRaisesRegex(LinkRefused, "not switched on"):
            create_for_loan(self.l2, self.a2)

    @override_settings(EASEBUZZ_ENABLED=False)
    def test_switched_off(self):
        with mock.patch("payments.easebuzz.requests.post") as post:
            with self.assertRaises(LinkRefused):
                create_for_loan(self.l2, self.a2)
            post.assert_not_called()


@override_settings(**CFG)
class StatusTests(P):
    def make(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            return create_for_loan(self.l2, self.a2)[0]

    def test_status_request_is_signed(self):
        link = self.make()
        with mock.patch("payments.easebuzz.requests.get", return_value=got()) as get:
            refresh(link)
        self.assertEqual(get.call_args.args[0], "https://testdashboard.easebuzz.in/easycollect/v1/get")
        q = get.call_args.kwargs["params"]
        self.assertEqual(q, {"key": "KEY1", "merchant_txn": link.merchant_txn, "hash": sha("KEY1", link.merchant_txn, "SALT1")})

    def test_unpaid_stays_active(self):
        link = self.make()
        with mock.patch("payments.easebuzz.requests.get", return_value=got(0)):
            self.assertFalse(refresh(link))
        link.refresh_from_db()
        self.assertEqual((link.state, link.lms_state), ("active", ""))
        self.assertIsNotNone(link.checked_at)

    def test_paid_is_recorded_once_with_timeline_and_waiting_for_lms(self):
        link = self.make()
        with mock.patch("payments.easebuzz.requests.get", return_value=got(1, "paid", "06-11-2026 15:04:05")):
            self.assertTrue(refresh(link))
            self.assertFalse(refresh(link))                   # a second check changes nothing and adds no second timeline line
        link.refresh_from_db()
        self.assertEqual((link.state, link.lms_state), ("paid", "pending"))
        self.assertEqual(timezone.localtime(link.paid_at).strftime("%d-%m-%Y %H:%M:%S"), "06-11-2026 15:04:05")
        self.assertEqual(Activity.objects.filter(loan=self.l2, detail__startswith="Payment of").count(), 1)

    def test_past_expiry_becomes_expired(self):
        link = self.make()
        PaymentLink.objects.filter(pk=link.pk).update(expires_on=timezone.localdate() - timedelta(days=1))
        link.refresh_from_db()
        with mock.patch("payments.easebuzz.requests.get", return_value=got(0)):
            self.assertTrue(refresh(link))
        link.refresh_from_db()
        self.assertEqual(link.state, "expired")

    def test_command_checks_only_open_recent_links(self):
        a = self.make()
        PaymentLink.objects.create(loan=self.l1, merchant_txn="OLD1", amount=1, payment_url=URL, state="paid")
        out = StringIO()
        with mock.patch("payments.easebuzz.requests.get", return_value=got(1, "paid")) as get:
            call_command("check_payment_links", stdout=out)
        self.assertEqual(get.call_count, 1)
        a.refresh_from_db()
        self.assertEqual(a.state, "paid")
        self.assertIn("checked 1, changed 1", out.getvalue())

    def test_command_survives_an_easebuzz_error(self):
        self.make()
        out, err = StringIO(), StringIO()
        with mock.patch("payments.easebuzz.requests.get", return_value=Resp({}, 500)):
            call_command("check_payment_links", stdout=out, stderr=err)
        self.assertIn("could not check 1", out.getvalue())


@override_settings(**CFG)
class ApiTests(P):
    def test_agent_creates_and_lists_on_own_account_only(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            r = self.api(self.a2).post("/api/payments/link", {"loan": self.l2.pk}, format="json")
            self.assertEqual(r.status_code, 201, r.content)
            self.assertEqual(r.json()["url"], URL)
            again = self.api(self.a2).post("/api/payments/link", {"loan": self.l2.pk}, format="json")
            self.assertEqual(again.status_code, 200)
            self.assertEqual(self.api(self.a1).post("/api/payments/link", {"loan": self.l2.pk}, format="json").status_code, 404)
        d = self.api(self.a2).get(f"/api/payments/link?loan={self.l2.pk}").json()
        self.assertEqual((len(d["links"]), d["preview"]["amount"], d["preview"]["stale"], d["preview"]["problem"]), (1, "8980", False, ""))
        self.assertEqual(self.api(self.a1).get(f"/api/payments/link?loan={self.l2.pk}").status_code, 404)
        self.assertTrue(AuditLog.objects.filter(action="payment_link_created").exists())
        self.assertEqual(self.api().post("/api/payments/link", {"loan": self.l2.pk}, format="json").status_code, 403)

    def test_stale_is_409_then_confirmed(self):
        Loan.objects.filter(pk=self.l2.pk).update(closure_as_on=timezone.localdate() - timedelta(days=1))
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            r = self.api(self.a2).post("/api/payments/link", {"loan": self.l2.pk}, format="json")
            self.assertEqual((r.status_code, r.json()["stale"]), (409, True))
            self.assertEqual(self.api(self.a2).post("/api/payments/link", {"loan": self.l2.pk, "confirm_stale": True}, format="json").status_code, 201)

    def test_refresh_endpoint(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            lid = self.api(self.a2).post("/api/payments/link", {"loan": self.l2.pk}, format="json").json()["id"]
        with mock.patch("payments.easebuzz.requests.get", return_value=got(1, "paid")):
            r = self.api(self.a2).post(f"/api/payments/link/{lid}/refresh")
            self.assertEqual((r.status_code, r.json()["state"]), (200, "paid"))
            self.assertEqual(self.api(self.a1).post(f"/api/payments/link/{lid}/refresh").status_code, 404)
        with mock.patch("payments.easebuzz.requests.get", return_value=Resp({}, 500)):
            PaymentLink.objects.filter(pk=lid).update(state="active")
            self.assertEqual(self.api(self.a2).post(f"/api/payments/link/{lid}/refresh").status_code, 502)

    def test_meta_says_whether_links_are_on(self):
        self.assertTrue(self.api(self.a2).get("/api/meta").json()["payments_enabled"])
        with override_settings(EASEBUZZ_ENABLED=False):
            self.assertFalse(self.api(self.a2).get("/api/meta").json()["payments_enabled"])

    def test_timeline_shows_a_payment_with_no_user(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            link, _ = create_for_loan(self.l2, self.a2)
        with mock.patch("payments.easebuzz.requests.get", return_value=got(1, "paid")):
            refresh(link)
        r = self.api(self.a2).get(f"/api/loans/{self.l2.pk}/timeline")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("received through the payment link" in x["detail"] for x in r.json()))


@override_settings(MSG91_SMS_ENABLED=True, MSG91_AUTHKEY="k", PAYMENT_LINK_DEFAULT="https://dummy.example/x", **CFG)
class MessagesUseTheRealLinkTests(P):
    def setUp(self):
        super().setUp()
        p = mock.patch("messaging.service.in_window", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_without_a_link_the_sms_refuses_and_never_falls_back_to_the_dummy(self):
        r = preview_sms(self.l2)
        self.assertTrue(r["problem"])
        with self.assertRaises(SendRefused):
            send_sms_for_loan(self.l2, self.a2)

    def test_with_todays_link_the_sms_carries_it(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            create_for_loan(self.l2, self.a2)
        self.assertEqual(preview_sms(self.l2)["problem"], "")
        with mock.patch("messaging.msg91.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"type": "success", "message": "r1"}
            m = send_sms_for_loan(self.l2, self.a2)
        self.assertEqual(m.state, "sent")
        self.assertEqual(post.call_args.kwargs["json"]["recipients"][0]["url"], URL)

    def test_a_yesterdays_link_is_not_used(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            link, _ = create_for_loan(self.l2, self.a2)
        PaymentLink.objects.filter(pk=link.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.assertTrue(preview_sms(self.l2)["problem"])

    def test_a_paid_link_is_not_used(self):
        with mock.patch("payments.easebuzz.requests.post", return_value=created()):
            link, _ = create_for_loan(self.l2, self.a2)
        PaymentLink.objects.filter(pk=link.pk).update(state="paid")
        self.assertTrue(preview_sms(self.l2)["problem"])

    @override_settings(EASEBUZZ_ENABLED=False)
    def test_with_easebuzz_off_the_default_link_still_works(self):
        self.assertEqual(preview_sms(self.l2)["problem"], "")
