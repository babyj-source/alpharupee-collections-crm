"""One-time codes: we generate and verify the code ourselves; MSG91 only delivers the SMS."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .models import OtpChallenge

log = logging.getLogger(__name__)


class OtpDeliveryError(Exception):
    pass


class OtpRateLimited(Exception):
    pass


def _hash(user_id, code):
    msg = f"{user_id}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def send_sms(mobile, code):
    """Deliver the code. Patched out in tests."""
    if settings.OTP_DEV_MODE:
        log.warning("OTP_DEV_MODE: code for %s is %s", mobile[-4:], code)
        return
    if not settings.MSG91_AUTHKEY or not settings.MSG91_OTP_TEMPLATE_ID:
        raise OtpDeliveryError("MSG91 is not configured")
    try:
        resp = requests.post(
            settings.MSG91_OTP_URL,
            params={"template_id": settings.MSG91_OTP_TEMPLATE_ID, "mobile": mobile, "otp": code},
            headers={"authkey": settings.MSG91_AUTHKEY, "Content-Type": "application/json"},
            json={settings.MSG91_OTP_VARIABLE: code},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise OtpDeliveryError("MSG91 request failed") from exc
    ok, reason = resp.status_code == 200, ""
    try:
        body = resp.json()
        ok = ok and body.get("type") == "success"
        reason = str(body.get("message", ""))[:120]
    except ValueError:
        ok = False
    if not ok:
        # Only MSG91's own short reason is logged (for example an unapproved template or a blocked IP), never the number or code.
        log.error("MSG91 refused the OTP (HTTP %s): %s", resp.status_code, reason or "no message")
        raise OtpDeliveryError("MSG91 refused the request")


def issue(user):
    """Create a fresh challenge, invalidate older ones and send the SMS."""
    now = timezone.now()
    recent = OtpChallenge.objects.filter(user=user, created_at__gte=now - timedelta(hours=1)).count()
    if recent >= settings.OTP_MAX_SENDS_PER_HOUR:
        raise OtpRateLimited()
    code = f"{secrets.randbelow(1_000_000):06d}"
    send_sms(user.mobile, code)  # raise before storing so a failed send costs no quota
    OtpChallenge.objects.filter(user=user, used=False).update(used=True)
    OtpChallenge.objects.create(
        user=user,
        code_hash=_hash(user.pk, code),
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
    )


def verify(user, code):
    """True if the code is right. Counts attempts and burns the challenge after too many."""
    ch = OtpChallenge.objects.filter(user=user, used=False).order_by("-created_at").first()
    if ch is None or ch.expires_at <= timezone.now():
        return False
    ch.attempts += 1
    if ch.attempts > settings.OTP_MAX_ATTEMPTS:
        ch.used = True
        ch.save(update_fields=["attempts", "used"])
        return False
    ok = hmac.compare_digest(ch.code_hash, _hash(user.pk, (code or "").strip()))
    if ok:
        ch.used = True
    ch.save(update_fields=["attempts", "used"])
    return ok
