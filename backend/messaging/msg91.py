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
    # Payment-link templates ask MSG91 to shorten the link (short_url 1) so a long payment link fits the DLT variable length.
    # Other templates carry no link, so shortening stays off for them.
    shorten = "payment_link" in template.variables.values()
    body = {
        "template_id": template.msg91_template_id,
        "short_url": "1" if shorten else "0",
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


# ---------------------------------------------------------------------------------------------------------------- WhatsApp
# The request shapes follow MSG91's WhatsApp API as remembered (template bulk endpoint, plain-text session endpoint) and the
# variable names (header_1, body_1 ...) in the approved template files. They have NOT been checked against MSG91's documentation.

def whatsapp_enabled():
    return bool(settings.MSG91_WHATSAPP_ENABLED and settings.MSG91_AUTHKEY and settings.MSG91_WHATSAPP_NUMBER)


def _find(obj, keys):
    """First value found under any of `keys`, looking through nested dicts and lists."""
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k) not in (None, "", [], {}):
                return obj[k]
        for v in obj.values():
            r = _find(v, keys)
            if r not in (None, ""):
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, keys)
            if r not in (None, ""):
                return r
    return None


def _wa_post(url, body):
    if not whatsapp_enabled():
        raise Msg91Disabled("WhatsApp sending is switched off")
    headers = {"authkey": settings.MSG91_AUTHKEY, "Content-Type": "application/json", "Accept": "application/json"}
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise Msg91Error("MSG91 request failed") from exc
    try:
        data = resp.json()
    except ValueError:
        data = {}
    d = data if isinstance(data, dict) else {}
    message = str(d.get("message") or d.get("error") or d.get("errors") or "")[:120]
    bad = (str(d.get("status", "")).lower() in ("fail", "failed", "error") or str(d.get("type", "")).lower() == "error"
           or d.get("success") is False or d.get("hasError") is True)
    log.info("MSG91 WhatsApp replied (HTTP %s): %s", resp.status_code,
             {k: d[k] for k in ("status", "type", "success", "hasError", "message") if k in d and not isinstance(d[k], (dict, list))} or "(no verdict fields)")
    if resp.status_code != 200 or bad:
        raise Msg91Error(f"MSG91 refused (HTTP {resp.status_code}): {message or 'no message'}")
    ref = _find(d, ("message_uuid", "uuid", "request_id", "messageId", "message_id", "id"))
    return str(ref)[:120] if ref else ""


def send_whatsapp_template(template, mobile, components):
    """components: {"body_1": {"type": "text", "value": "..."}, "header_1": {"type": "image", "value": "https://..."}}"""
    body = {
        "integrated_number": settings.MSG91_WHATSAPP_NUMBER,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template.name,
                "language": {"code": template.language, "policy": "deterministic"},
                "namespace": template.namespace,
                "to_and_components": [{"to": [mobile], "components": components}],
            },
        },
    }
    return _wa_post(settings.MSG91_WHATSAPP_TEMPLATE_URL, body)


def send_whatsapp_text(mobile, text):
    """Free text, allowed only while the customer's 24-hour window is open."""
    body = {"integrated_number": settings.MSG91_WHATSAPP_NUMBER, "recipient_number": mobile, "content_type": "text", "text": text}
    return _wa_post(settings.MSG91_WHATSAPP_TEXT_URL, body)


def send_whatsapp_media(mobile, media_type, link, caption="", filename=""):
    """A picture or document inside the 24-hour window. `link` is a public https address MSG91 fetches the file from.
    NOT checked against MSG91's documentation: the first shape is WhatsApp Cloud API style (`image: {link, caption}`); if MSG91 refuses
    it with a 4xx, the flat shape (`media_url`) is tried once. The reply MSG91 gives is kept on the message so a wrong shape shows up."""
    inner = {"link": link}
    if caption:
        inner["caption"] = caption
    if media_type == "document" and filename:
        inner["filename"] = filename
    base = {"integrated_number": settings.MSG91_WHATSAPP_NUMBER, "recipient_number": mobile, "content_type": media_type}
    flat = dict(base, media_url=link, url=link)
    if caption:
        flat["caption"] = caption
    if filename:
        flat["filename"] = filename
    try:
        return _wa_post(settings.MSG91_WHATSAPP_MEDIA_URL or settings.MSG91_WHATSAPP_TEXT_URL, dict(base, **{media_type: inner}))
    except Msg91Error as first:
        if not str(first).startswith("MSG91 refused (HTTP 4"):
            raise
        log.warning("MSG91 refused the nested media shape (%s); trying the flat one", first)
        return _wa_post(settings.MSG91_WHATSAPP_MEDIA_URL or settings.MSG91_WHATSAPP_TEXT_URL, flat)


def set_whatsapp_call_settings(enabled, call_hours=None):
    """MSG91 'WhatsApp Voice Call' settings: switch calling on or off for the business number. Returns (HTTP status, MSG91's message).
    Settings only: MSG91 documents no endpoint that places a call. The body is from the doc plus `integrated_number`, which the
    doc's 400 text ('Integrated number missing') implies but does not show, so the reply is returned as it is for the operator to read."""
    if not settings.MSG91_AUTHKEY or not settings.MSG91_WHATSAPP_NUMBER:
        raise Msg91Error("MSG91_AUTHKEY and MSG91_WHATSAPP_NUMBER must be set first")
    body = {"integrated_number": settings.MSG91_WHATSAPP_NUMBER, "status": "ENABLED" if enabled else "DISABLED",
            "call_hours": call_hours or {"status": "DISABLED"}}
    headers = {"authkey": settings.MSG91_AUTHKEY, "Content-Type": "application/json", "Accept": "application/json"}
    try:
        resp = requests.post(settings.MSG91_WHATSAPP_CALL_SETTINGS_URL, json=body, headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise Msg91Error("MSG91 request failed") from exc
    try:
        d = resp.json()
    except ValueError:
        d = {}
    msg = str(d.get("message") or d.get("error") or d.get("errors") or "")[:300] if isinstance(d, dict) else ""
    return resp.status_code, msg
