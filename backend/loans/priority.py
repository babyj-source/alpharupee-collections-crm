"""Who to call first. A transparent points score: every point has a reason the agent can read on the screen.

Higher is more urgent. The numbers are starting values for Collections leadership to tune, not signed-off policy."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ACTIVE_Q, Loan
from .serializers import LoanSerializer, open_ptp
from .workflow import BUCKET_KEYS, bucket_key

# (highest DPD in the band, points)
DPD_POINTS = [(0, 5), (7, 25), (15, 40), (30, 55), (60, 65), (10**6, 70)]
AMOUNT_FULL_POINTS = 20          # reached at or above this overdue amount
AMOUNT_FULL_AT = Decimal(50000)
CALLBACK_DUE, CALLBACK_TODAY = 50, 20
PTP_LAPSED, PTP_TODAY, PTP_HOLD = 45, 40, -30
NEVER_CALLED, STALE_CALL, CALLED_TODAY, STALE_DAYS = 15, 10, -10, 3
IN_DISPUTE = -20
MAX_ROWS = 500


def score_loan(loan, now, today):
    """Return (points, reasons) for one account."""
    pts, reasons = 0, []
    dpd = loan.dpd or 0
    for top, p in DPD_POINTS:
        if dpd <= top:
            pts += p
            break
    reasons.append(f"{dpd} DPD" if dpd else "Due, not yet late")

    overdue = loan.overdue_amount
    pts += int(AMOUNT_FULL_POINTS * min(Decimal(1), overdue / AMOUNT_FULL_AT))

    if loan.callback_at:
        if loan.callback_at <= now:
            pts += CALLBACK_DUE
            reasons.append("Call-back due")
        elif timezone.localtime(loan.callback_at).date() == today:
            pts += CALLBACK_TODAY
            reasons.append("Call-back today")

    p = open_ptp(loan)
    if p is not None:
        if p.promised_date < today:
            pts += PTP_LAPSED
            reasons.append("Promise missed")
        elif p.promised_date == today:
            pts += PTP_TODAY
            reasons.append("Promise due today")
        else:
            pts += PTP_HOLD
            reasons.append("Promise given")

    if loan.last_disposition_at is None:
        pts += NEVER_CALLED
        reasons.append("Never called")
    else:
        last = timezone.localtime(loan.last_disposition_at).date()
        if last == today:
            pts += CALLED_TODAY
            reasons.append("Called today")
        elif (today - last) >= timedelta(days=STALE_DAYS):
            pts += STALE_CALL
            reasons.append(f"Not called for {(today - last).days} days")

    if loan.crm_status == "dispute":
        pts += IN_DISPUTE
        reasons.append("In dispute")
    return pts, reasons


def ranked(loans, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    out = []
    for loan in loans:
        pts, reasons = score_loan(loan, now, today)
        out.append((pts, loan.overdue_amount, loan, reasons))
    out.sort(key=lambda t: (-t[0], -t[1], t[2].account_id))
    return out


class PriorityView(APIView):
    """Ranked call list: the agent's own overdue accounts, or every overdue account for the head."""

    def get(self, request):
        from .views import scoped  # local import: views imports this module's neighbours

        p = request.query_params
        qs = scoped(request.user).filter(ACTIVE_Q).filter(Q(demand_due__gt=0) | Q(fee_due__gt=0) | Q(dpd__gt=0))
        if p.get("bucket") in BUCKET_KEYS:
            _k, _label, lo, hi = BUCKET_KEYS[p["bucket"]]
            qs = qs.filter(dpd__gte=lo)
            if hi is not None:
                qs = qs.filter(dpd__lte=hi)
        if p.get("agent") == "none" and request.user.is_head:
            qs = qs.filter(assigned_agent__isnull=True)
        elif p.get("agent") and request.user.is_head:
            try:
                qs = qs.filter(assigned_agent_id=int(p["agent"]))
            except ValueError:
                return Response({"detail": "agent must be a number."}, status=400)
        try:
            limit = max(1, min(MAX_ROWS, int(p.get("limit", 100))))
        except ValueError:
            return Response({"detail": "limit must be a number."}, status=400)
        rows = ranked(list(qs))
        data = []
        for pts, _amt, loan, reasons in rows[:limit]:
            d = LoanSerializer(loan).data
            d["score"], d["reasons"], d["bucket"] = pts, reasons, bucket_key(loan.dpd or 0)
            data.append(d)
        return Response({"count": len(rows), "results": data})
