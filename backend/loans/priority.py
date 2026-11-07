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

# The built-in points. The Admin can change any of them (Priority weights screen); a changed value is stored in PriorityConfig.
# dpd_points: (highest DPD in the band, points), amount: full points at or above `amount_full_at`.
DEFAULT_WEIGHTS = {
    "dpd_points": [[0, 5], [7, 25], [15, 40], [30, 55], [60, 65], [1000000, 70]],
    "amount_full_points": 20, "amount_full_at": 50000,
    "callback_due": 50, "callback_today": 20,
    "ptp_lapsed": 45, "ptp_today": 40, "ptp_hold": -30,
    "never_called": 15, "stale_call": 10, "called_today": -10, "stale_days": 3,
    "in_dispute": -20,
}
LABELS = {
    "dpd_points": "Points for days past due (per band)", "amount_full_points": "Most points for a large overdue amount",
    "amount_full_at": "Overdue amount that earns those points (₹)", "callback_due": "Call-back is due now",
    "callback_today": "Call-back is later today", "ptp_lapsed": "Promise date has passed", "ptp_today": "Promise is due today",
    "ptp_hold": "Promise still to come (negative: ring later)", "never_called": "Never called", "stale_call": "Not called for the days below",
    "called_today": "Already called today (negative)", "stale_days": "Days without a call that count as stale", "in_dispute": "In dispute (negative)",
}
MAX_ROWS = 500


def get_weights():
    """The defaults with the Admin's changes on top."""
    from .models import PriorityConfig
    row = PriorityConfig.objects.filter(pk=1).first()
    w = {**DEFAULT_WEIGHTS}
    if row and isinstance(row.values, dict):
        w.update({k: v for k, v in row.values.items() if k in DEFAULT_WEIGHTS})
    return w


def clean_weights(data):
    """Validate an Admin edit: returns (values, error). Every key must be a known one; points are whole numbers, not absurd."""
    if not isinstance(data, dict):
        return None, "Send the weights as an object."
    out = {}
    for k, v in data.items():
        if k not in DEFAULT_WEIGHTS:
            return None, f"Unknown weight “{k}”."
        if k == "dpd_points":
            if not (isinstance(v, list) and 1 <= len(v) <= 10 and all(isinstance(b, list) and len(b) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in b) for b in v)):
                return None, "Days-past-due points must be pairs of whole numbers: highest day in the band, points."
            tops = [b[0] for b in v]
            if tops != sorted(tops) or len(set(tops)) != len(tops) or tops[0] < 0:
                return None, "Days-past-due bands must go up (for example 0, 7, 15, 30) with no repeats."
            if any(abs(b[1]) > 200 for b in v):
                return None, "Points must be between -200 and 200."
            out[k] = v
            continue
        if isinstance(v, bool) or not isinstance(v, int):
            return None, f"“{LABELS[k]}” must be a whole number."
        lo, hi = (1, 100000000) if k == "amount_full_at" else (1, 365) if k == "stale_days" else (-200, 200)
        if not lo <= v <= hi:
            return None, f"“{LABELS[k]}” must be between {lo} and {hi}."
        out[k] = v
    return out, ""


def score_loan(loan, now, today, w=None):
    """Return (points, reasons) for one account."""
    w = w or get_weights()
    pts, reasons = 0, []
    dpd = loan.dpd or 0
    for top, p in w["dpd_points"]:
        if dpd <= top:
            pts += p
            break
    reasons.append(f"{dpd} DPD" if dpd else "Due, not yet late")

    overdue = loan.overdue_amount
    pts += int(w["amount_full_points"] * min(Decimal(1), overdue / Decimal(w["amount_full_at"])))

    if loan.callback_at:
        if loan.callback_at <= now:
            pts += w["callback_due"]
            reasons.append("Call-back due")
        elif timezone.localtime(loan.callback_at).date() == today:
            pts += w["callback_today"]
            reasons.append("Call-back today")

    p = open_ptp(loan)
    if p is not None:
        if p.promised_date < today:
            pts += w["ptp_lapsed"]
            reasons.append("Promise missed")
        elif p.promised_date == today:
            pts += w["ptp_today"]
            reasons.append("Promise due today")
        else:
            pts += w["ptp_hold"]
            reasons.append("Promise given")

    if loan.last_disposition_at is None:
        pts += w["never_called"]
        reasons.append("Never called")
    else:
        last = timezone.localtime(loan.last_disposition_at).date()
        if last == today:
            pts += w["called_today"]
            reasons.append("Called today")
        elif (today - last) >= timedelta(days=w["stale_days"]):
            pts += w["stale_call"]
            reasons.append(f"Not called for {(today - last).days} days")

    if loan.crm_status == "dispute":
        pts += w["in_dispute"]
        reasons.append("In dispute")
    return pts, reasons


def ranked(loans, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    w = get_weights()
    out = []
    for loan in loans:
        pts, reasons = score_loan(loan, now, today, w)
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


class WeightsView(APIView):
    """GET (head): the points in use, the built-in defaults and a label for each. PUT (Admin): change some or all of them, or {"reset": true}."""

    def get_permissions(self):
        from accounts.permissions import IsAdmin, IsHead
        return [IsAdmin()] if self.request.method == "PUT" else [IsHead()]

    def get(self, request):
        from .models import PriorityConfig
        row = PriorityConfig.objects.filter(pk=1).first()
        return Response({"weights": get_weights(), "defaults": DEFAULT_WEIGHTS, "labels": LABELS,
                         "changed": bool(row and row.values), "updated_at": row.updated_at if row and row.values else None,
                         "updated_by": row.updated_by.name if row and row.values and row.updated_by else ""})

    def put(self, request):
        from accounts.models import audit
        from .models import PriorityConfig
        row, _ = PriorityConfig.objects.get_or_create(pk=1)
        if request.data.get("reset") is True:
            row.values, row.updated_by = {}, request.user
            row.save()
            audit(request, "priority_weights_reset", user=request.user)
            return self.get(request)
        values, err = clean_weights(request.data.get("weights"))
        if err:
            return Response({"detail": err}, status=400)
        row.values = {**row.values, **values}
        row.updated_by = request.user
        row.save()
        audit(request, "priority_weights_changed", user=request.user, detail=", ".join(sorted(values))[:280])
        return self.get(request)
