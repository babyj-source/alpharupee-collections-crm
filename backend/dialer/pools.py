"""Who is in each campaign pool right now. Pools never overlap: a call-back beats a promise beats the DPD bucket, and an account
with a call-back or promise still to come is held back from the buckets so nobody is dialled twice."""
from datetime import datetime, time

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from loans.models import ACTIVE_Q, Loan, Ptp
from loans.phones import normalise_mobile
from loans.workflow import BUCKETS

POOLS = [(key, label) for key, label, _lo, _hi in BUCKETS] + [("ptp", "Promise to pay"), ("callback", "Call-back")]
POOL_KEYS = [k for k, _ in POOLS]


def _open_ptp(**extra):
    return Ptp.objects.filter(loan=OuterRef("pk"), status=Ptp.State.OPEN, **extra)


def _base():
    """Who may be dialled at all: active loan, something overdue, a valid mobile, not paid or in dispute."""
    return (Loan.objects.filter(ACTIVE_Q).filter(borrower_mobile__regex=r"^91[6-9][0-9]{9}$")
            .filter(Q(demand_due__gt=0) | Q(fee_due__gt=0)).exclude(crm_status__in=["paid", "dispute"]))


def pool_loans(key, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    eod = timezone.make_aware(datetime.combine(today, time.max))
    qs = _base()
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


RULE_DEFAULTS = {"dpd_min": 0, "dpd_max": None, "min_overdue": 0, "product_code": "", "hold_promises": True, "hold_callbacks": True}


def clean_rules(raw):
    """Validate a custom campaign's rules. Returns (rules, error)."""
    raw = raw if isinstance(raw, dict) else {}
    r = dict(RULE_DEFAULTS)
    try:
        r["dpd_min"] = int(raw.get("dpd_min") or 0)
        mx = raw.get("dpd_max")
        r["dpd_max"] = None if mx in (None, "") else int(mx)
        r["min_overdue"] = int(raw.get("min_overdue") or 0)
    except (TypeError, ValueError):
        return None, "Days past due and the overdue amount must be whole numbers."
    r["product_code"] = str(raw.get("product_code") or "").strip()[:40]
    r["hold_promises"] = bool(raw.get("hold_promises", True))
    r["hold_callbacks"] = bool(raw.get("hold_callbacks", True))
    if r["dpd_min"] < 0 or (r["dpd_max"] is not None and r["dpd_max"] < r["dpd_min"]) or r["min_overdue"] < 0:
        return None, "Check the numbers: the highest DPD cannot be below the lowest, and nothing can be negative."
    return r, ""


def rules_summary(c):
    if not c.custom:
        return ""
    r = {**RULE_DEFAULTS, **(c.rules or {})}
    hi = "and above" if r["dpd_max"] is None else f"to {r['dpd_max']}"
    parts = [f"DPD {r['dpd_min']} {hi}"]
    if r["min_overdue"]:
        parts.append(f"overdue at least Rs {r['min_overdue']:,}")
    if r["product_code"]:
        parts.append(f"product {r['product_code']}")
    parts.append("promises and call-backs still to come are held back" if r["hold_promises"] and r["hold_callbacks"]
                 else "includes accounts with a promise or call-back" if not (r["hold_promises"] or r["hold_callbacks"]) else "partly holds promises or call-backs")
    return " · ".join(parts)


def campaign_loans(camp, now=None):
    """Customers in a campaign right now. Built-in campaigns never overlap; custom ones follow only their own rules,
    so a customer can be in more than one if the rules overlap."""
    if not camp.custom:
        return pool_loans(camp.key, now)
    r = {**RULE_DEFAULTS, **(camp.rules or {})}
    qs = _base().filter(dpd__gte=r["dpd_min"])
    if r["dpd_max"] is not None:
        qs = qs.filter(dpd__lte=r["dpd_max"])
    if r["min_overdue"]:
        qs = qs.annotate(_over=F("demand_due") + F("fee_due")).filter(_over__gte=r["min_overdue"])
    if r["product_code"]:
        qs = qs.filter(product_code__iexact=r["product_code"])
    if r["hold_callbacks"]:
        qs = qs.filter(callback_at__isnull=True)
    if r["hold_promises"]:
        qs = qs.exclude(Exists(_open_ptp()))
    return qs


def lead_pairs(qs):
    """(loan id, Smartflo lead row). The lead list must have columns in this order: Mobile, Name, Account ID."""
    out = []
    for loan in qs.order_by("-dpd", "account_id"):
        mobile = normalise_mobile(loan.borrower_mobile)
        if not mobile:
            continue
        out.append((loan.pk, {"field_0": mobile[2:], "field_1": (loan.borrower_name or "")[:60], "field_2": loan.account_id}))
    return out


def lead_rows(qs):
    return [row for _pk, row in lead_pairs(qs)]
