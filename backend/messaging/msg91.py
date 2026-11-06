"""MSG91 SMS (Flow API). Same auth key as the login OTP. Everything stays OFF until MSG91_SMS_ENABLED=true."""
import logging

import requests
from django.conf import settings

log = logging.getLogger(__name__)


class Msg91Disabled(Exception):
    pass


class Msg91Error(Exception):
    pass


def send_sms(template, mobile, variables):
    """Send one template SMS and return MSG91's request id. `variables` is {MSG91 variable: text}."""
    if not settings.MSG91_SMS_ENABLED:
        raise Msg91Disabled("SMS sending is switched off")
    if not settings.MSG91_AUTHKEY:
        raise Msg91Error("MSG91 is not configured")
    body = {
        "template_id": template.msg91_template_id,
        "short_url": "0",
        "recipients": [{"mobiles": mobile, **variables}],
    }
    headers = {"authkey": settings.MSG91_AUTHKEY, "Content-Type": "application/json", "Accept": "application/json"}
    try:
        resp = requests.post(settings.MSG91_FLOW_URL, json=body, headers=headers, timeout=10)
    except requests.RequestException as exc:
        raise Msg91Error("MSG91 request failed") from exc
    ok, message = resp.status_code == 200, ""
    try:
        data = resp.json()
        ok = ok and data.get("type") == "success"
        message = str(data.get("message", ""))[:120]
    except ValueError:
        ok = False
    if not ok:
        log.error("MSG91 refused an SMS (HTTP %s): %s", resp.status_code, message or "no message")   # never the number or text
        raise Msg91Error(f"MSG91 refused (HTTP {resp.status_code}): {message or 'no message'}")
    return message
