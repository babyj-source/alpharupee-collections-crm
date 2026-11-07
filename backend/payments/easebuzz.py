"""Easebuzz EasyCollect (payment links), from https://docs.easebuzz.in : "Sub-merchant Create/Update Link" and "Get Easycollect Link".

Create: POST {base}/easycollect/v1/create (same call for an ordinary merchant; `sub_merchant_id` is sent only when configured), JSON, hash = sha512(key|merchant_txn|name|email|phone|amount|udf1..udf5|message|salt).
Status: GET {base}/easycollect/v1/get?key=&merchant_txn=&hash=  with hash = sha512(key|merchant_txn|salt).
Sandbox base is https://testdashboard.easebuzz.in, production https://dashboard.easebuzz.in. The salt never leaves the server and is never logged."""
import hashlib
import logging

import requests
from django.conf import settings

log = logging.getLogger(__name__)


class EasebuzzDisabled(Exception):
    pass


class EasebuzzError(Exception):
    """Easebuzz refused or could not be reached. The text is safe to show the user."""


def enabled():
    return bool(settings.EASEBUZZ_ENABLED and settings.EASEBUZZ_KEY and settings.EASEBUZZ_SALT)     # the sub-merchant id is optional


def _sha512(*parts):
    return hashlib.sha512("|".join(parts).encode()).hexdigest()


def create_hash(p):
    return _sha512(settings.EASEBUZZ_KEY, p["merchant_txn"], p["name"], p["email"], p["phone"], p["amount"],
                   p["udf1"], p["udf2"], p["udf3"], p["udf4"], p["udf5"], p["message"], settings.EASEBUZZ_SALT)


def status_hash(merchant_txn):
    return _sha512(settings.EASEBUZZ_KEY, merchant_txn, settings.EASEBUZZ_SALT)


def _check():
    if not settings.EASEBUZZ_ENABLED:
        raise EasebuzzDisabled("Payment links are switched off on this server.")
    if not enabled():
        raise EasebuzzError("Easebuzz is not configured on this server.")


def _detail(resp, body):
    """What Easebuzz said, shortened, so a refusal can be understood. Easebuzz's answer never contains our salt."""
    if isinstance(body, dict):
        parts = [f"{k}: {v}" for k, v in body.items() if k != "status" and v not in (None, "", [], {})]
        text = "; ".join(parts)
    else:
        text = str(body)
    return text[:300]


def _answer(resp):
    try:
        body = resp.json()
    except ValueError:
        raise EasebuzzError(f"Easebuzz sent an answer the CRM cannot read (HTTP {resp.status_code}).")
    if resp.status_code != 200 or not isinstance(body, dict) or not body.get("status"):
        detail = _detail(resp, body)
        log.warning("easebuzz refused (HTTP %s): %s", resp.status_code, detail)
        raise EasebuzzError(f"Easebuzz did not accept the request (HTTP {resp.status_code}){': ' + detail if detail else ''}.")
    return body.get("data") or {}


def create_link(*, merchant_txn, name, phone, amount, udf1, udf2, message, expiry_date, email=""):
    """Make one payment link. `amount` is a string like "8980.00". Returns Easebuzz's `data` (has payment_url)."""
    _check()
    p = {"key": settings.EASEBUZZ_KEY, "merchant_txn": merchant_txn, "name": name, "email": email, "phone": phone, "amount": amount,
         "udf1": udf1, "udf2": udf2, "udf3": "", "udf4": "", "udf5": "", "message": message}
    p["hash"] = create_hash(p)
    p["expiry_date"] = expiry_date
    if settings.EASEBUZZ_SUB_MERCHANT_ID:      # only for sub-aggregator merchants; Easebuzz refuses it (ECSUBME03) for an ordinary merchant
        p["sub_merchant_id"] = settings.EASEBUZZ_SUB_MERCHANT_ID
    try:
        resp = requests.post(f"{settings.EASEBUZZ_BASE_URL}/easycollect/v1/create", json=p, headers={"Accept": "application/json"}, timeout=20)
    except requests.RequestException as exc:
        log.warning("easebuzz create failed: %s", type(exc).__name__)
        raise EasebuzzError("Easebuzz could not be reached. Try again in a moment.")
    data = _answer(resp)
    if not str(data.get("payment_url") or "").startswith("https://"):
        raise EasebuzzError("Easebuzz did not return a payment link.")
    return data


def get_link(merchant_txn):
    """Ask Easebuzz where a link stands (payment_made, state). Returns Easebuzz's `data`."""
    _check()
    params = {"key": settings.EASEBUZZ_KEY, "merchant_txn": merchant_txn, "hash": status_hash(merchant_txn)}
    try:
        resp = requests.get(f"{settings.EASEBUZZ_BASE_URL}/easycollect/v1/get", params=params, headers={"Accept": "application/json"}, timeout=20)
    except requests.RequestException as exc:
        log.warning("easebuzz status failed: %s", type(exc).__name__)
        raise EasebuzzError("Easebuzz could not be reached. Try again in a moment.")
    return _answer(resp)
