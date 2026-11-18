"""Post one paid payment link to the LMS as a repayment: Encore's "flow collection" API, POST .../postTransactionListDirect.

The request body is the one Sensei sent as a sample (one transaction, transactionName "Repayment", amount in `amount1`, the bank
account and our payment reference in `reference`). The LMS user and password come from the server's settings and are never logged.

The answer is judged cautiously, because a wrong "posted" would hide a repayment that the LMS never got, and a wrong "retry" could
post it twice. So the result is one of:
  ok         the LMS said, in so many words, that it took it
  rejected   the LMS said no (or the user is not allowed): nothing was posted
  not_sent   the request never left (could not connect): nothing was posted
  unclear    anything else, including a timeout or a reply we do not recognise: the LMS may or may not have it. A person checks first."""
import json
import logging
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)

FAIL_WORDS = ("fail", "error", "reject", "invalid", "denied", "not allowed", "duplicate", "exception")


class NotReady(Exception):
    """Posting is switched off or a setting is missing. The text says which."""


def missing():
    """The first setting that stops posting, as a sentence, or ''."""
    if not settings.LMS_POST_ENABLED:
        return "Posting to the LMS is switched off on this server."
    for name, label in (("LMS_POST_URL", "the LMS address"), ("LMS_POST_USER", "the LMS user"), ("LMS_POST_PASSWORD", "the LMS password"),
                        ("LMS_POST_ACCOUNT_CODE", "the bank account code"), ("LMS_POST_INSTRUMENT", "the instrument")):
        if not getattr(settings, name):
            return f"Posting to the LMS is not set up: {label} is missing."
    return ""


def enabled():
    return not missing()


def _amount(value):
    d = Decimal(value)
    return str(int(d)) if d == d.to_integral_value() else f"{d:.2f}"


def build_body(link):
    """The JSON the LMS is sent for one paid link."""
    paid_on = timezone.localdate(link.paid_at or timezone.now())
    return {
        "transactions": [{
            "transactionId": None, "valueDateStr": paid_on.isoformat(), "transactionDateStr": None, "systemDateAndTimeStr": None,
            "accountId": link.loan.account_id, "entityId": None, "sequenceNum": -1, "transactionName": "Repayment",
            "part1": None, "part2": 0, "part3": None, "part4": None, "part5": None, "part6": None, "part7": None, "part8": None,
            "amount": None, "amount1": _amount(link.amount), "amount2": None, "amount3": None,
            "description": "Repayment through payment link", "userId": settings.LMS_POST_USER_ID or settings.LMS_POST_USER,
            "customerName": None, "status": None, "responseCode": None, "response": None, "payeeAccountId": None,
            "param1": None, "param2": None, "param2Str": None, "param3": None, "param4": None, "param5": None,
            "instrument": settings.LMS_POST_INSTRUMENT,
            "reference": f"accountCode:{settings.LMS_POST_ACCOUNT_CODE}|instrumentRef:{link.merchant_txn}",
            "transactionLotId": None, "currencyCode": "INR", "originalTransactionDateStr": None, "originalTransactionId": None,
            "originalUserId": None, "receiptId": None,
        }],
        "safeMode": False, "runReversedTransactions": True, "actionType": "EXECUTION", "status": None,
        "transactionFormat": "Default", "commitSize": 1,
    }


def _text(v):
    return "" if v is None else str(v).strip().lower()


def judge(status_code, body):
    """-> (result, note). `body` is the parsed JSON (or None)."""
    ok_words = {w.strip().lower() for w in settings.LMS_POST_OK_WORDS.split(",") if w.strip()}
    if status_code in (401, 403):
        return "rejected", f"The LMS refused the user or password (HTTP {status_code})."
    if 400 <= status_code < 500:
        return "rejected", f"The LMS did not accept the request (HTTP {status_code}). {_snip(body)}"
    if status_code >= 500 or not 200 <= status_code < 300:
        return "unclear", f"The LMS answered HTTP {status_code}; it may or may not have taken the repayment. {_snip(body)}"
    txns = body if isinstance(body, list) else (body.get("transactions") if isinstance(body, dict) else None)
    if not isinstance(txns, list) or not txns:
        return "unclear", f"The LMS answered HTTP {status_code} in a form the CRM does not recognise. {_snip(body)}"
    top = (_text(body.get("status")) + " " + _text(body.get("response")) + " " + _text(body.get("responseCode"))) if isinstance(body, dict) else ""
    seen_ok = True
    for t in txns:
        t = t if isinstance(t, dict) else {}
        said = " ".join(_text(t.get(k)) for k in ("status", "responseCode", "response"))
        if any(w in said for w in FAIL_WORDS):
            return "rejected", f"The LMS rejected the repayment. {_snip(t)}"
        if _text(t.get("status")) not in ok_words and _text(t.get("responseCode")) not in ok_words:
            seen_ok = False
    if any(w in top for w in FAIL_WORDS):
        return "rejected", f"The LMS rejected the repayment. {_snip(body)}"
    if seen_ok:
        return "ok", "The LMS took the repayment."
    return "unclear", f"The LMS answered HTTP {status_code} but did not say plainly that it took the repayment. {_snip(txns[0] if isinstance(txns[0], dict) else {})}"


def _snip(body):
    """A short, safe summary of the LMS's answer for the Admin: only status words and codes, never the whole body."""
    if isinstance(body, list):
        body = body[0] if body and isinstance(body[0], dict) else {}
    if isinstance(body, dict):
        keys = ("status", "responseCode", "response", "message", "error", "errorCode", "description")
        parts = [f"{k}: {str(body[k])[:100]}" for k in keys if body.get(k) not in (None, "")]
        return ("; ".join(parts) or "No status in the answer.")[:200]
    return str(body or "")[:200]


def post(link):
    """Send the repayment. Returns (result, note, the LMS's whole answer). Never raises for an LMS answer."""
    why = missing()
    if why:
        raise NotReady(why)
    try:
        resp = requests.post(settings.LMS_POST_URL, json=build_body(link), auth=(settings.LMS_POST_USER, settings.LMS_POST_PASSWORD),
                             headers={"Accept": "application/json"}, timeout=(5, settings.LMS_POST_TIMEOUT_SECONDS))
    except requests.ConnectionError as exc:
        # Includes "could not resolve" and "refused": the request did not get there. A timeout while connecting is the same.
        log.warning("LMS post not sent: %s", type(exc).__name__)
        if isinstance(exc, requests.ConnectTimeout) or "Connection aborted" not in str(exc):
            return "not_sent", "The LMS could not be reached. Nothing was posted.", ""
        return "unclear", "The connection to the LMS broke while posting; it may or may not have taken the repayment.", ""
    except requests.RequestException as exc:
        log.warning("LMS post unclear: %s", type(exc).__name__)
        return "unclear", "The LMS did not answer in time; it may or may not have taken the repayment.", ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    result, note = judge(resp.status_code, body)
    log.info("LMS post for %s: HTTP %s -> %s", link.merchant_txn, resp.status_code, result)
    reply = f"HTTP {resp.status_code} " + (json.dumps(body, default=str) if body is not None else (getattr(resp, "text", "") or "")[:2000])
    return result, note, reply[:4000]
