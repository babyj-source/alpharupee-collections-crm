"""Send one template SMS for a loan: pick the template, fill it, check the rules, send, log."""
from django.conf import settings
from django.utils import timezone

from loans.workflow import CALL_END_HOUR, CALL_START_HOUR

from . import msg91
from .models import MessageLog, SmsTemplate
from .templates import MissingValue, pick_bucket, render, variables_for


class SendRefused(Exception):
    """The message must not go out; the text says why and is safe to show the user."""


def in_window(now=None):
    h = timezone.localtime(now or timezone.now()).hour
    return CALL_START_HOUR <= h < CALL_END_HOUR      # same 08:00-19:00 window as calls


def _prepare(loan, template_key, link, now):
    """Pick the template and build the message, or raise SendRefused. Shared by the preview and the send so they never disagree."""
    link = (link or settings.PAYMENT_LINK_DEFAULT or "").strip() or None
    if template_key:
        template = SmsTemplate.objects.filter(key=template_key, active=True).first()
        if template is None:
            raise SendRefused("That SMS template is not available.")
    else:
        template = pick_bucket(loan.dpd)
        if template is None:
            raise SendRefused("No SMS template is switched on for this loan's days past due.")
    if not in_window(now):
        raise SendRefused(f"Messages go out between {CALL_START_HOUR:02d}:00 and {CALL_END_HOUR:02d}:00 only.")
    if loan.crm_status in ("paid", "dispute"):
        raise SendRefused("This account is marked paid or in dispute.")
    mobile = loan.borrower_mobile
    if not (mobile and len(mobile) == 12 and mobile.startswith("91") and mobile[2] in "6789"):
        raise SendRefused("The borrower has no valid mobile number.")
    if link and not link.startswith("https://"):
        raise SendRefused("The payment link must start with https://")
    try:
        values = variables_for(template, loan, link)
    except MissingValue as exc:
        raise SendRefused(f"Cannot build this message: {exc}.") from exc
    today = timezone.localdate(now) if now else timezone.localdate()
    if MessageLog.objects.filter(loan=loan, template_key=template.key, state="sent", created_at__date=today).exists():
        raise SendRefused("This message was already sent to this borrower today.")
    return template, values, mobile


def preview_sms(loan, template_key=None, link=None, now=None):
    """What would be sent now, as {template, name, text, problem}. `problem` says why it cannot go (text is then empty)."""
    try:
        template, values, _ = _prepare(loan, template_key, link, now)
    except SendRefused as exc:
        return {"template": template_key or "", "name": "", "text": "", "problem": str(exc)}
    return {"template": template.key, "name": template.name, "text": render(template, values), "problem": ""}


def send_sms_for_loan(loan, user, template_key=None, link=None, now=None):
    template, values, mobile = _prepare(loan, template_key, link, now)
    text = render(template, values)
    log = MessageLog(loan=loan, template_key=template.key, msg91_template_id=template.msg91_template_id,
                     mobile_last4=mobile[-4:], text=text, sent_by=user)
    try:
        log.provider_ref = msg91.send_sms(template, mobile, values)
        log.state = MessageLog.State.SENT
    except msg91.Msg91Disabled:
        raise SendRefused("SMS sending is switched off.")
    except msg91.Msg91Error as exc:
        log.state, log.error = MessageLog.State.FAILED, str(exc)[:200]
    log.save()
    return log
