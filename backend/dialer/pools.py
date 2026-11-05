"""Who is in each campaign pool right now. Pools never overlap: a call-back beats a promise beats the DPD bucket, and an account
with a call-back or promise still to come is held back from the buckets so nobody is dialled twice."""
from datetime import datetime, time

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from loans.models import ACTIVE_Q, Loan, Ptp
from loans.phones import normalise_mobile
from loans.workflow import BUCKETS

POOLS = [(key, label) for key, label, _lo, _hi in BUCKETS] + [("ptp", "Promise to pay"), ("callback", "Call-back")]
POOL_KEYS = [k for k, _ in POOLS]


def _open_ptp(**extra):
    return Ptp.objects.filter(loan=OuterRef("pk"), status=Ptp.State.OPEN, **extra)


def pool_loans(key, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    eod = timezone.make_aware(datetime.combine(today, time.max))
    qs = (Loan.objects.filter(ACTIVE_Q).exclude(borrower_mobile="").filter(Q(demand_due__gt=0) | Q(fee_due__gt=0))
          .exclude(crm_status__in=["paid", "dispute"]))
    if key == "callback":
        return qs.filter(callback_at__isnull=False, callback_at__lte=eod)
    qs = qs.filter(callback_at__isnull=True)
    if key == "ptp":
        return qs.filter(Exists(_open_ptp(promised_date__lte=today)))
    for k, _label, lo, hi in BUCKETS:
        if k == key:
            qs = qs.exclude(Exists(_open_ptp())).filter(dpd__gte=lo)
            return qs.filter(dpd__lte=hi) if hi is not None else qs
    raise KeyError(key)


def lead_rows(qs):
    """Smartflo lead rows. The lead list must have columns in this order: Mobile, Name, Account ID."""
    rows = []
    for loan in qs.order_by("-dpd", "account_id"):
        mobile = normalise_mobile(loan.borrower_mobile)
        if not mobile:
            continue
        rows.append({"field_0": mobile[2:], "field_1": (loan.borrower_name or "")[:60], "field_2": loan.account_id})
    return rows
