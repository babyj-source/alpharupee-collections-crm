"""WhatsApp: template messages, two-way chat inside the 24-hour window, and MSG91's webhook."""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from loans.models import Activity, Loan
from loans.workflow import CALL_END_HOUR, CALL_START_HOUR

from . import media, msg91
from .models import WhatsappMedia, WhatsappMessage, WhatsappTemplate, WhatsappWebhookLog
from .service import _default_link, RepeatNeeded, SendRefused, in_window
from .templates import MissingValue, render, variables_for

log = logging.getLogger(__name__)


def _valid_mobile(mobile):
    return bool(mobile and len(mobile) == 12 and mobile.startswith("91") and mobile[2] in "6789")


def session_open(loan, now=None):
    """True while the customer wrote to us within WHATSAPP_SESSION_HOURS, the only time WhatsApp allows free text."""
    now = now or timezone.now()
    last = WhatsappMessage.objects.filter(loan=loan, direction="in").order_by("-created_at").first()
    return bool(last and last.created_at >= now - timedelta(hours=settings.WHATSAPP_SESSION_HOURS)), last


# --------------------------------------------------------------------------------------------------------------- templates
def _prepare(loan, template_key, link, now, again=False):
    link = (link or _default_link(loan) or "").strip() or None
    if template_key:
        template = WhatsappTemplate.objects.filter(key=template_key, active=True).first()
        if template is None:
            raise SendRefused("That WhatsApp template is not available.")
    else:
        template = WhatsappTemplate.objects.filter(kind="overdue", active=True).first() if (loan.dpd or 0) >= 1 else None
        if template is None:
            raise SendRefused("No WhatsApp template fits this loan yet. The due-date reminders need the next EMI date, which the CRM does not hold.")
    if not in_window(now):
        raise SendRefused(f"Messages go out between {CALL_START_HOUR:02d}:00 and {CALL_END_HOUR:02d}:00 only.")
    if loan.crm_status in ("paid", "dispute"):
        raise SendRefused("This account is marked paid or in dispute.")
    if not _valid_mobile(loan.borrower_mobile):
        raise SendRefused("The borrower has no valid mobile number.")
    if link and not link.startswith("https://"):
        raise SendRefused("The payment link must start with https://")
    try:
        values = variables_for(template, loan, link)
    except MissingValue as exc:
        raise SendRefused(f"Cannot build this message: {exc}.") from exc
    today = timezone.localdate(now) if now else timezone.localdate()
    sent = WhatsappMessage.objects.filter(loan=loan, direction="out", kind="template", created_at__date=today).exclude(state="failed")
    if sent.count() >= settings.WHATSAPP_MAX_PER_LOAN_PER_DAY:
        raise SendRefused(f"This borrower has already had {settings.WHATSAPP_MAX_PER_LOAN_PER_DAY} WhatsApp messages today. Try again tomorrow.")
    earlier = sent.filter(template_key=template.key).order_by("-created_at").first()
    if earlier and not again:
        raise RepeatNeeded(f"This message was already sent to this borrower today at {timezone.localtime(earlier.created_at):%H:%M}.")
    return template, values


def preview_whatsapp(loan, template_key=None, link=None, now=None):
    warning = ""
    try:
        try:
            template, values = _prepare(loan, template_key, link, now)
        except RepeatNeeded as exc:
            warning = str(exc)
            template, values = _prepare(loan, template_key, link, now, again=True)
    except SendRefused as exc:
        return {"template": template_key or "", "name": "", "text": "", "problem": str(exc), "warning": ""}
    return {"template": template.key, "name": template.name, "text": render(template, values), "problem": "", "warning": warning,
            "image": template.has_image_header}


def send_whatsapp_for_loan(loan, user, template_key=None, link=None, now=None, again=False):
    template, values = _prepare(loan, template_key, link, now, again)
    components = {f"body_{n}": {"type": "text", "value": v} for n, v in values.items()}
    if template.has_image_header:
        components["header_1"] = {"type": "image", "value": template.header_image_url}
    msg = WhatsappMessage(loan=loan, direction="out", kind="template", template_key=template.key, text=render(template, values),
                          mobile_last4=loan.borrower_mobile[-4:], sent_by=user)
    try:
        msg.provider_ref = msg91.send_whatsapp_template(template, loan.borrower_mobile, components)
        msg.state = "sent"
    except msg91.Msg91Disabled:
        raise SendRefused("WhatsApp sending is switched off.")
    except msg91.Msg91Error as exc:
        msg.state, msg.error = "failed", str(exc)[:200]
    msg.save()
    Activity.objects.create(loan=loan, user=user, kind=Activity.Kind.MESSAGE,
                            detail=f"WhatsApp {'sent' if msg.state == 'sent' else 'failed'} to the borrower: {template.name}")
    return msg


# ------------------------------------------------------------------------------------------------------------------- chat
def send_chat_text(loan, user, text, now=None):
    text = (text or "").strip()
    if not text:
        raise SendRefused("Type a message first.")
    if len(text) > 1000:
        raise SendRefused("Keep the message under 1000 characters.")
    if not msg91.whatsapp_enabled():
        raise SendRefused("WhatsApp sending is switched off.")
    if not _valid_mobile(loan.borrower_mobile):
        raise SendRefused("The borrower has no valid mobile number.")
    ok, _ = session_open(loan, now)
    if not ok:
        raise SendRefused(f"WhatsApp only allows free text within {settings.WHATSAPP_SESSION_HOURS} hours of the customer's last message. Send a template instead.")
    msg = WhatsappMessage(loan=loan, direction="out", kind="text", text=text, mobile_last4=loan.borrower_mobile[-4:], sent_by=user)
    try:
        msg.provider_ref = msg91.send_whatsapp_text(loan.borrower_mobile, text)
        msg.state = "sent"
    except msg91.Msg91Disabled:
        raise SendRefused("WhatsApp sending is switched off.")
    except msg91.Msg91Error as exc:
        msg.state, msg.error = "failed", str(exc)[:200]
    msg.save()
    return msg


def send_chat_attachment(loan, user, upload, caption="", base_url=""):
    """A picture or document to the customer, inside the 24-hour window. MSG91 fetches the file from a short-lived public link."""
    caption = (caption or "").strip()
    if len(caption) > 1000:
        raise SendRefused("Keep the caption under 1000 characters.")
    if not msg91.whatsapp_enabled():
        raise SendRefused("WhatsApp sending is switched off.")
    if not _valid_mobile(loan.borrower_mobile):
        raise SendRefused("The borrower has no valid mobile number.")
    ok, _ = session_open(loan)
    if not ok:
        raise SendRefused(f"WhatsApp only allows attachments within {settings.WHATSAPP_SESSION_HOURS} hours of the customer's last message. Send a template instead.")
    try:
        mtype, name, mime, data = media.check_upload(upload)
    except media.BadFile as exc:
        raise SendRefused(str(exc)) from exc
    base = settings.PUBLIC_BASE_URL or base_url
    if not base.startswith("https://"):
        raise SendRefused("The CRM's public https address is not set (PUBLIC_BASE_URL), so WhatsApp cannot fetch the file.")
    msg = WhatsappMessage(loan=loan, direction="out", kind="media", text=caption, media_type=mtype, media_name=name, media_mime=mime,
                          mobile_last4=loan.borrower_mobile[-4:], sent_by=user)
    msg.save()
    WhatsappMedia.objects.create(message=msg, content=data, size=len(data))
    try:
        msg.provider_ref = msg91.send_whatsapp_media(loan.borrower_mobile, mtype, media.public_link(msg, base), caption, name)
        msg.state = "sent"
    except msg91.Msg91Disabled:
        msg.delete()
        raise SendRefused("WhatsApp sending is switched off.")
    except msg91.Msg91Error as exc:
        msg.state, msg.error = "failed", str(exc)[:200]
    msg.save()
    return msg


# ----------------------------------------------------------------------------------------------------------------- webhook
_DIGITS = re.compile(r"\d{10,}")
_NUMBER_KEYS = ("customerNumber", "customer_number", "from", "wa_id", "waId", "mobile", "mobile_number", "recipient", "contact", "msisdn")
_TEXT_KEYS = ("text", "body", "message_text", "content", "caption")
_REF_KEYS = ("message_uuid", "uuid", "messageId", "message_id", "request_id", "id")
_STATUS_VALUES = {"sent": "sent", "delivered": "delivered", "read": "read", "failed": "failed", "undelivered": "failed",
                  "delivery_failed": "failed", "rejected": "failed"}


def _mask(text):
    return _DIGITS.sub(lambda m: m.group()[:2] + "x" * (len(m.group()) - 4) + m.group()[-2:], text)


def _text_of(payload):
    v = msg91._find(payload, _TEXT_KEYS)
    if isinstance(v, dict):
        v = msg91._find(v, ("body", "text"))
    return str(v).strip() if v else ""


def _loan_for(number):
    last10 = re.sub(r"\D", "", str(number or ""))[-10:]
    if len(last10) < 10:
        return None
    qs = Loan.objects.filter(Q(borrower_mobile__endswith=last10) | Q(alt_mobile__endswith=last10))
    recent = WhatsappMessage.objects.filter(loan__in=qs, direction="out").order_by("-created_at").first()
    return recent.loan if recent else qs.order_by("-dpd", "-id").first()


def handle_webhook(payload):
    """Apply one MSG91 webhook body. Returns a short label for the log: inbound, status, ignored."""
    payload = media.expand(payload)
    direction = str(msg91._find(payload, ("direction",)) or "").lower()
    ref = msg91._find(payload, _REF_KEYS)
    status = str(msg91._find(payload, ("status", "event", "message_status")) or "").lower().replace(" ", "_")
    mapped = _STATUS_VALUES.get(status)
    if mapped and ref:
        n = WhatsappMessage.objects.filter(provider_ref=str(ref), direction="out").update(
            state=mapped, error=(str(msg91._find(payload, ("reason", "error", "errors", "description")) or "")[:200] if mapped == "failed" else ""))
        if n:
            return "status"
    if direction in ("out", "outgoing", "outbound"):
        return "ignored"
    number = msg91._find(payload, _NUMBER_KEYS)
    ctype = str(msg91._find(payload, ("contentType", "content_type", "type")) or "").lower()
    file = None
    if ctype in media.MEDIA_TYPES or ctype in ("", "media", "file", "attachment", "message"):
        file = media.inbound_media(payload, ctype)
    kind, text = "text", ""
    if file:
        kind, text = "media", file["caption"]
    elif ctype in ("button", "interactive", "button_reply", "list_reply", "quick_reply") or (ctype not in ("", "text", "template") and media.button_text(payload)):
        kind, text = "button", media.button_text(payload) or _text_of(payload) or "Tapped a button"
    elif ctype in ("", "text", "template"):
        text = _text_of(payload)
    else:
        kind, text = "media", media.other_text(payload, ctype)
    if number and (text or file):
        loan = _loan_for(number)
        last4 = re.sub(r"\D", "", str(number))[-4:]
        if ref and WhatsappMessage.objects.filter(provider_ref=str(ref), direction="in").exists():
            return "ignored"          # MSG91 retried the same message
        m = WhatsappMessage.objects.create(
            loan=loan, direction="in", kind=kind, text=text[:2000], state="received", provider_ref=str(ref or "")[:120], mobile_last4=last4, seen=False,
            media_type=file["type"] if file else "", media_name=file["name"] if file else "", media_mime=file["mime"] if file else "",
            media_url=file["url"][:2000] if file else "")
        if file and file["url"]:
            try:
                media.fetch_inbound(m)       # keep the file now, while MSG91's link is fresh; if it fails the chat tries again when opened
            except Exception:
                log.exception("Could not keep the WhatsApp file of message %s", m.pk)
        return "inbound" if loan else "inbound-unmatched"
    return "ignored"


def log_webhook(raw, handled):
    WhatsappWebhookLog.objects.create(body=_mask(raw)[:4000], handled=handled)
    old = list(WhatsappWebhookLog.objects.order_by("-id").values_list("id", flat=True)[200:])
    if old:
        WhatsappWebhookLog.objects.filter(id__in=old).delete()
