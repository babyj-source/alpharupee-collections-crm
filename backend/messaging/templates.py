"""Which SMS template a loan gets and what goes into its variables. No network calls here."""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import SmsTemplate, WhatsappTemplate

PAYMENT_LINK = "payment_link"


class MissingValue(Exception):
    """A variable cannot be filled (for example no payment link yet). Nothing must be sent."""


def _rupees(v):
    return str(int(Decimal(v or 0).quantize(Decimal("1"))))


def _date(d):
    if not isinstance(d, date):
        raise MissingValue("no date for this loan")
    return d.strftime("%d-%m-%Y")


def _wa_date(d):
    if not isinstance(d, date):
        raise MissingValue("no date for this loan")
    return d.strftime("%d-%b-%Y")      # 05-Oct-2026, the way the approved WhatsApp samples show it


def _first_name(loan):
    first = (loan.borrower_name or "").strip().split(" ")[0]
    if not first:
        raise MissingValue("the borrower has no name")
    return first.title()


# loan field name -> function(loan, link) -> text for the SMS
SOURCES = {
    "closure_amount": lambda loan, link: _rupees(loan.closure_amount),   # the day's closure amount, whole rupee
    "installment_amount": lambda loan, link: _rupees(loan.installment_amount),   # the EMI
    "dpd": lambda loan, link: str(max(int(loan.dpd or 0), 0)),
    "account_id": lambda loan, link: loan.account_id,
    "oldest_due_date": lambda loan, link: _date(loan.oldest_due_date),
    PAYMENT_LINK: lambda loan, link: link or _missing("payment link"),
    "first_name": lambda loan, link: _first_name(loan),
}


def _missing(what):
    raise MissingValue(f"{what} is not available")


def pick_bucket(dpd):
    """The active bucket/NPA template whose days-past-due range holds this loan's DPD, or None."""
    dpd = max(int(dpd or 0), 0)
    for t in SmsTemplate.objects.filter(active=True, kind__in=("bucket", "npa")):
        low = t.dpd_min if t.dpd_min is not None else 0
        if dpd >= low and (t.dpd_max is None or dpd <= t.dpd_max):
            return t
    return None


def pick_whatsapp(days_to_due):
    """The WhatsApp template for a loan whose next EMI falls in `days_to_due` days (2, 1, 0), or the missed-payment one when
    that number is negative. None when the matching template is switched off."""
    q = WhatsappTemplate.objects.filter(active=True)
    return q.filter(kind="overdue").first() if days_to_due < 0 else q.filter(days_before_due=days_to_due).first()


def variables_for(template, loan, link=None, today=None):
    """{variable: text}. Raises MissingValue when a needed value does not exist, so we never send half a message."""
    if getattr(template, "has_image_header", False) and not template.header_image_url:
        raise MissingValue("this template needs a header image URL")
    out = {}
    for var, field in template.variables.items():
        if field == "rule_due_date":     # the reminder's own day: today + 2, + 1 or + 0
            day = (today or timezone.localdate()) + timedelta(days=template.days_before_due or 0)
            out[var] = _wa_date(day)
        elif field == "oldest_due_date" and isinstance(template, WhatsappTemplate):
            out[var] = _wa_date(loan.oldest_due_date)
        elif field in SOURCES:
            out[var] = SOURCES[field](loan, link)
        else:
            raise MissingValue(f"unknown field {field!r} for variable {var}")
    return out


def render(template, values):
    """The text the borrower will receive (for previews and the message log). Handles ##name## and {{1}} markers."""
    text = re.sub(r"##(\w+)##", lambda m: str(values.get(m.group(1), m.group(0))), template.body)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), text)
