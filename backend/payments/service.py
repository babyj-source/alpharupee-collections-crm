"""Make a payment link for a loan, and keep its state in step with Easebuzz."""
import re
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

from loans.models import Activity
from loans.phones import normalise_mobile

from accounts.models import audit

from . import easebuzz, lms
from .models import PaymentLink


class LinkRefused(Exception):
    """The link must not be made; the text says why and is safe to show the user."""


class StaleClosure(LinkRefused):
    """The closure amount is not for today (for example the LMS sync is stopped). It can still be used if the sender confirms."""


def _today(now=None):
    return timezone.localdate(now or timezone.now())


def closure_amount(loan):
    return Decimal(loan.closure_amount or 0).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def active_link(loan, now=None):
    """The newest link of today that is still waiting for payment, or None. Messages only ever carry this one."""
    today = _today(now)
    for link in loan.payment_links.filter(state=PaymentLink.State.ACTIVE).order_by("-created_at", "-id"):
        if link.created_at and timezone.localdate(link.created_at) == today and not (link.expires_on and link.expires_on < today):
            return link
    return None


def link_url_for_messages(loan):
    """The https link an SMS/WhatsApp should carry, or None."""
    link = active_link(loan)
    return link.payment_url if link else None


def preview(loan, now=None):
    amount, as_on = closure_amount(loan), loan.closure_as_on
    out = {"amount": str(amount), "as_on": as_on.isoformat() if as_on else None, "stale": bool(as_on is None or as_on != _today(now)),
           "problem": "", "enabled": easebuzz.enabled()}
    try:
        _check_loan(loan, amount)
    except LinkRefused as exc:
        out["problem"] = str(exc)
    return out


def _check_loan(loan, amount):
    if loan.operational_status.strip().lower() not in ("0", "active"):
        raise LinkRefused("This account is closed.")
    if loan.crm_status in ("paid", "dispute"):
        raise LinkRefused("This account is marked paid or in dispute.")
    if amount <= 0:
        raise LinkRefused("The closure amount is zero, so there is nothing to collect.")
    if not normalise_mobile(loan.borrower_mobile):
        raise LinkRefused("The borrower has no valid mobile number.")


def _name(loan):
    return (re.sub(r"[^A-Za-z0-9 ]", " ", loan.borrower_name or "").strip() or "Borrower")[:60]


def create_for_loan(loan, user, confirm_stale=False, now=None):
    """Make (or reuse) today's payment link for the loan. Returns (link, created)."""
    now = now or timezone.now()
    amount = closure_amount(loan)
    _check_loan(loan, amount)
    if not easebuzz.enabled():
        raise LinkRefused("Payment links are not switched on on this server.")
    existing = active_link(loan, now)
    if existing and existing.amount == amount:
        return existing, False          # same day, same amount: reuse it instead of making a second link
    if loan.closure_as_on != _today(now) and not confirm_stale:
        as_on = loan.closure_as_on.strftime("%d-%m-%Y") if loan.closure_as_on else "an unknown date"
        raise StaleClosure(f"The closure amount shown is as on {as_on}, not today. The LMS figures may be out of date.")
    expires = _today(now) + timedelta(days=int(settings.PAYMENT_LINK_VALID_DAYS))
    txn = "ARC" + uuid.uuid4().hex[:20].upper()
    try:
        data = easebuzz.create_link(
            merchant_txn=txn, name=_name(loan), phone=normalise_mobile(loan.borrower_mobile)[2:], amount=f"{amount:.2f}",
            udf1="CRM", udf2=loan.account_id, message="Loan repayment", expiry_date=expires.strftime("%d-%m-%Y"))
    except easebuzz.EasebuzzDisabled as exc:
        raise LinkRefused(str(exc))
    except easebuzz.EasebuzzError as exc:
        raise LinkRefused(str(exc))
    link = PaymentLink.objects.create(loan=loan, merchant_txn=txn, amount=amount, closure_as_on=loan.closure_as_on, payment_url=data["payment_url"],
                                      expires_on=expires, easebuzz_state=str(data.get("state") or "")[:30], created_by=user)
    Activity.objects.create(loan=loan, user=user, kind=Activity.Kind.MESSAGE, detail=f"Payment link created for ₹{amount:,.0f}")
    return link, True


def _parse_dt(text):
    for f in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return timezone.make_aware(datetime.strptime(str(text), f))
        except (ValueError, TypeError):
            continue
    return None


def refresh(link, now=None):
    """Ask Easebuzz about one link and update it. Returns True when its state changed. Raises EasebuzzError when Easebuzz cannot be asked."""
    now = now or timezone.now()
    data = easebuzz.get_link(link.merchant_txn)
    link.checked_at = now
    link.easebuzz_state = str(data.get("state") or "")[:30]
    changed = False
    try:
        paid = int(data.get("payment_made") or 0) > 0
    except (TypeError, ValueError):
        paid = False
    if paid and not link.easebuzz_txn_id:
        link.easebuzz_txn_id = str(data.get("transaction_id") or "").strip()[:40]
    if paid and link.state != PaymentLink.State.PAID:
        link.state, link.paid_at, link.lms_state = PaymentLink.State.PAID, _parse_dt(data.get("quick_pay_transaction_date")) or now, PaymentLink.Lms.PENDING
        changed = True
        Activity.objects.create(loan=link.loan, user=None, kind=Activity.Kind.MESSAGE, detail=f"Payment of ₹{link.amount:,.0f} received through the payment link")
    elif not paid and link.state == PaymentLink.State.ACTIVE and (link.easebuzz_state.lower() == "expired" or (link.expires_on and link.expires_on < _today(now))):
        link.state = PaymentLink.State.EXPIRED
        changed = True
    link.save()
    return changed


# ------------------------------------------------------------------------------------------------ posting to the LMS
LMS_AUTO_TRIES = 5          # a repayment that could not even be sent is tried again each minute, this many times
LMS_STALE_MINUTES = 10      # "being posted" for longer than this means the CRM stopped halfway: nobody knows if the LMS has it


def easebuzz_id(link):
    """Easebuzz's own id of this link's payment ("transaction_id" in its status answer), kept on the link. '' when Easebuzz does not give one."""
    if link.easebuzz_txn_id:
        return link.easebuzz_txn_id
    try:
        tid = str(easebuzz.get_link(link.merchant_txn).get("transaction_id") or "").strip()[:40]
    except (easebuzz.EasebuzzError, easebuzz.EasebuzzDisabled):
        return ""
    if tid:
        PaymentLink.objects.filter(pk=link.pk).update(easebuzz_txn_id=tid)
        link.easebuzz_txn_id = tid
    return tid


def post_to_lms(link, user=None, again=False, now=None):
    """Post one paid link to the LMS as a repayment, at most once at a time. `again=True` (an Admin, on a failed one) also takes a
    failed link. Returns the link. Whatever the LMS says is kept on the link; a repayment is never posted twice by the CRM on its own."""
    now = now or timezone.now()
    if link.state != PaymentLink.State.PAID:
        raise LinkRefused("This payment link has not been paid.")
    allowed = [PaymentLink.Lms.PENDING] + ([PaymentLink.Lms.FAILED] if again else [])
    if link.lms_state in allowed and not easebuzz_id(link):
        # The LMS is sent Easebuzz's transaction id; without it nothing is posted (it is looked up again on the next run).
        PaymentLink.objects.filter(pk=link.pk, lms_state=link.lms_state).update(lms_note="Waiting for Easebuzz's transaction id before posting to the LMS.")
        link.refresh_from_db()
        return link
    if not PaymentLink.objects.filter(pk=link.pk, state=PaymentLink.State.PAID, lms_state__in=allowed).update(lms_state=PaymentLink.Lms.POSTING, lms_tried_at=now):
        link.refresh_from_db()
        return link                                       # already posted, being posted, or not allowed to be posted again
    link.refresh_from_db()
    try:
        result, note, reply = lms.post(link)
    except lms.NotReady as exc:
        PaymentLink.objects.filter(pk=link.pk).update(lms_state=PaymentLink.Lms.PENDING, lms_note=str(exc)[:300])
        link.refresh_from_db()
        raise LinkRefused(str(exc))
    link.lms_tries += 1
    link.lms_note = note[:300]
    link.lms_reply = reply
    if result == "ok":
        link.lms_state, link.lms_posted_at = PaymentLink.Lms.POSTED, timezone.now()
        Activity.objects.create(loan=link.loan, user=None, kind=Activity.Kind.MESSAGE, detail=f"Repayment of ₹{link.amount:,.0f} posted to the LMS")
        audit(None, "lms_repayment_posted", user=user, object_type="loan", object_id=link.loan_id, detail=link.merchant_txn)
    elif result == "not_sent" and link.lms_tries < LMS_AUTO_TRIES:
        link.lms_state = PaymentLink.Lms.PENDING          # nothing left the server: safe to try again next minute
    else:
        link.lms_state = PaymentLink.Lms.FAILED
        audit(None, "lms_repayment_" + result, user=user, object_type="loan", object_id=link.loan_id, detail=link.merchant_txn)
    link.save(update_fields=["lms_state", "lms_tries", "lms_note", "lms_reply", "lms_posted_at"])
    return link


def post_pending(now=None, limit=20):
    """Called every minute: post the paid links that wait for the LMS, and stop any that were left half-sent. Returns (posted, failed, left)."""
    now = now or timezone.now()
    PaymentLink.objects.filter(lms_state=PaymentLink.Lms.POSTING, lms_tried_at__lt=now - timedelta(minutes=LMS_STALE_MINUTES)).update(
        lms_state=PaymentLink.Lms.FAILED,
        lms_note="The CRM stopped while posting, so it is not known whether the LMS has this repayment. Check the LMS before posting again.")
    if not lms.enabled():
        return 0, 0, PaymentLink.objects.filter(lms_state=PaymentLink.Lms.PENDING).count()
    posted = failed = 0
    for link in PaymentLink.objects.filter(state=PaymentLink.State.PAID, lms_state=PaymentLink.Lms.PENDING).select_related("loan").order_by("paid_at", "id")[:limit]:
        link = post_to_lms(link, now=now)
        posted += link.lms_state == PaymentLink.Lms.POSTED
        failed += link.lms_state == PaymentLink.Lms.FAILED
    return posted, failed, PaymentLink.objects.filter(lms_state=PaymentLink.Lms.PENDING).count()
