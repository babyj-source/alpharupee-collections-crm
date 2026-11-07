"""Easebuzz transaction webhook (https://docs.easebuzz.in "Transaction Webhook"): a form-urlencoded POST whenever a payment changes state.

The webhook is only a prompt: it never marks anything paid by itself. It finds the payment link it belongs to and then asks Easebuzz
(the signed status call, `service.refresh`) whether it was paid. A forged or odd call therefore cannot create a payment."""
import hashlib
import json

from django.conf import settings

from . import easebuzz
from .models import PaymentLink, PaymentWebhookLog
from .service import refresh

KEEP = ("txnid", "status", "amount", "udf1", "udf2", "mode", "easepayid", "error", "addedon", "bank_ref_num", "unmappedstatus")


def response_hash_ok(d):
    """Easebuzz's documented response hash: sha512(salt|status|udf10..udf1|email|firstname|productinfo|amount|txnid|key). Informational only."""
    try:
        parts = [settings.EASEBUZZ_SALT, d.get("status", "")] + [d.get(f"udf{i}", "") for i in range(10, 0, -1)] + [
            d.get("email", ""), d.get("firstname", ""), d.get("productinfo", ""), d.get("amount", ""), d.get("txnid", ""), d.get("key", "")]
        return hashlib.sha512("|".join(parts).encode()).hexdigest() == str(d.get("hash", "")).lower()
    except Exception:
        return False


def handle(d):
    """Check the link(s) this webhook is about. Returns a short note for the log."""
    txn = str(d.get("txnid", "")).strip()
    links = list(PaymentLink.objects.filter(merchant_txn=txn)) if txn else []
    if not links and str(d.get("udf1", "")).strip() == "CRM" and str(d.get("udf2", "")).strip():
        links = list(PaymentLink.objects.filter(state=PaymentLink.State.ACTIVE, loan__account_id=str(d["udf2"]).strip()))
    if not links:
        return "no matching link"
    changed = failed = 0
    for link in links:
        try:
            changed += 1 if refresh(link) else 0
        except easebuzz.EasebuzzError:
            failed += 1          # the one-minute check will try again
    return f"checked {len(links)}, changed {changed}" + (f", could not check {failed}" if failed else "")


def log(d, handled):
    body = {k: str(d.get(k, ""))[:80] for k in KEEP if d.get(k) not in (None, "")}
    body["hash_ok"] = response_hash_ok(d)
    PaymentWebhookLog.objects.create(body=json.dumps(body), handled=handled[:80])
    old = list(PaymentWebhookLog.objects.order_by("-id").values_list("id", flat=True)[200:])
    if old:
        PaymentWebhookLog.objects.filter(id__in=old).delete()
