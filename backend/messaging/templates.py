"""Which SMS template a loan gets and what goes into its variables. No network calls here."""
import re
from datetime import date
from decimal import Decimal

from .models import SmsTemplate

PAYMENT_LINK = "payment_link"


class MissingValue(Exception):
    """A variable cannot be filled (for example no payment link yet). Nothing must be sent."""


def _rupees(v):
    return str(int(Decimal(v or 0).quantize(Decimal("1"))))


def _date(d):
    if not isinstance(d, date):
        raise MissingValue("no date for this loan")
    return d.strftime("%d-%m-%Y")


# loan field name -> function(loan, link) -> text for the SMS
SOURCES = {
    "closure_amount": lambda loan, link: _rupees(loan.closure_amount),   # the day's closure amount, whole rupee
    "installment_amount": lambda loan, link: _rupees(loan.installment_amount),   # the EMI
    "dpd": lambda loan, link: str(max(int(loan.dpd or 0), 0)),
    "account_id": lambda loan, link: loan.account_id,
    "oldest_due_date": lambda loan, link: _date(loan.oldest_due_date),
    PAYMENT_LINK: lambda loan, link: link or _missing("payment link"),
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


def variables_for(template, loan, link=None):
    """{MSG91 variable: text}. Raises MissingValue when a needed value does not exist, so we never send half a message."""
    out = {}
    for var, field in template.variables.items():
        fn = SOURCES.get(field)
        if fn is None:
            raise MissingValue(f"unknown field {field!r} for variable {var}")
        out[var] = fn(loan, link)
    return out


def render(template, values):
    """The text the borrower will receive (for previews and the message log)."""
    return re.sub(r"##(\w+)##", lambda m: str(values.get(m.group(1), m.group(0))), template.body)
