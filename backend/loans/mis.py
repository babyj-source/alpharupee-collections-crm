"""Head's MIS: portfolio by bucket, call activity, promise performance and agent productivity.

Everything is counted from what the CRM and the LMS sync hold. Money collected is not here: repayments are not
synced from the LMS yet, so there is no collection-efficiency figure."""
from datetime import datetime, time, timedelta

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role, User
from accounts.permissions import IsHead

from .models import ACTIVE_Q, Activity, Loan, Ptp
from .workflow import BUCKETS, CONNECTED, DISPOSITION_LABELS, DISPOSITIONS

OVERDUE = ExpressionWrapper(F("demand_due") + F("fee_due"), output_field=DecimalField(max_digits=16, decimal_places=2))
PERIODS = {"1": 1, "7": 7, "30": 30, "90": 90}


def _rate(a, b):
    return round(100 * a / b, 1) if b else None


def period_start(today, days):
    return timezone.make_aware(datetime.combine(today - timedelta(days=days - 1), time.min))


def portfolio():
    qs = Loan.objects.filter(ACTIVE_Q)
    out = []
    for key, label, lo, hi in BUCKETS:
        b = qs.filter(dpd__gte=lo)
        if hi is not None:
            b = b.filter(dpd__lte=hi)
        a = b.aggregate(accounts=Count("id"), overdue=Sum(OVERDUE), principal=Sum("balance"), closure=Sum("closure_amount"),
                        unassigned=Count("id", filter=Q(assigned_agent__isnull=True)))
        out.append({"bucket": key, "label": label, "accounts": a["accounts"], "overdue_amount": a["overdue"] or 0,
                    "outstanding_principal": abs(a["principal"] or 0), "closure_amount": a["closure"] or 0,
                    "unassigned": a["unassigned"]})
    return out


def promises(start, today):
    """Promises whose date fell in the period. Kept rate = kept / due; a promise nobody closed counts as not kept."""
    qs = Ptp.objects.filter(promised_date__gte=start.date(), promised_date__lte=today).exclude(
        status__in=[Ptp.State.SUPERSEDED, Ptp.State.CANCELLED]
    )
    by = {r["status"]: r for r in qs.values("status").annotate(n=Count("id"), amt=Sum("amount"))}

    def n(s):
        return by.get(s, {}).get("n", 0)

    def amt(s):
        return by.get(s, {}).get("amt") or 0

    due = sum(r["n"] for r in by.values())
    open_due = qs.filter(status=Ptp.State.OPEN, promised_date__lte=today)
    return {
        "due": due, "kept": n("kept"), "broken": n("broken"), "still_open": n("open"),
        "kept_rate": _rate(n("kept"), due),
        "promised_amount": sum(r["amt"] or 0 for r in by.values()), "kept_amount": amt("kept"),
        "missed_open": open_due.filter(promised_date__lt=today).count(),
        "created": Ptp.objects.filter(created_at__gte=start).count(),
    }


def agent_rows(start, today):
    agents = list(User.objects.filter(role=Role.AGENT, is_active=True).order_by("name"))
    ids = [a.pk for a in agents]
    over = Loan.objects.filter(ACTIVE_Q, assigned_agent__in=ids).filter(Q(dpd__gt=0) | Q(demand_due__gt=0) | Q(fee_due__gt=0))
    assigned = {r["assigned_agent"]: r for r in over.values("assigned_agent").annotate(n=Count("id"), amt=Sum(OVERDUE))}
    callbacks = {r["assigned_agent"]: r["n"] for r in Loan.objects.filter(callback_at__isnull=False, assigned_agent__in=ids)
                 .values("assigned_agent").annotate(n=Count("id"))}
    acts = Activity.objects.filter(kind=Activity.Kind.DISPOSITION, created_at__gte=start, user__in=ids)
    calls = {r["user"]: r for r in acts.values("user").annotate(
        n=Count("id"), worked=Count("loan", distinct=True), connected=Count("id", filter=Q(disposition__in=CONNECTED)))}
    ptps = {r["agent"]: r for r in Ptp.objects.filter(created_at__gte=start, agent__in=ids)
            .values("agent").annotate(n=Count("id"), amt=Sum("amount"))}
    kept = {r["agent"]: r["n"] for r in Ptp.objects.filter(status=Ptp.State.KEPT, closed_at__gte=start, agent__in=ids)
            .values("agent").annotate(n=Count("id"))}
    rows = []
    for a in agents:
        c = calls.get(a.pk, {})
        n_calls, n_conn = c.get("n", 0), c.get("connected", 0)
        rows.append({
            "agent_id": a.pk, "agent": a.name,
            "assigned_accounts": assigned.get(a.pk, {}).get("n", 0), "assigned_overdue": assigned.get(a.pk, {}).get("amt") or 0,
            "accounts_worked": c.get("worked", 0), "calls": n_calls, "connected": n_conn, "contact_rate": _rate(n_conn, n_calls),
            "promises": ptps.get(a.pk, {}).get("n", 0), "promise_amount": ptps.get(a.pk, {}).get("amt") or 0,
            "promises_kept": kept.get(a.pk, 0), "callbacks_pending": callbacks.get(a.pk, 0),
        })
    return rows


class MisView(APIView):
    permission_classes = [IsHead]

    def get(self, request):
        days = PERIODS.get(request.query_params.get("days", "30"), 30)
        today = timezone.localdate()
        start = period_start(today, days)
        acts = Activity.objects.filter(kind=Activity.Kind.DISPOSITION, created_at__gte=start)
        total = acts.count()
        connected = acts.filter(disposition__in=CONNECTED).count()
        by_code = {r["disposition"]: r["n"] for r in acts.values("disposition").annotate(n=Count("id"))}
        outcomes = [
            {"code": code, "label": DISPOSITION_LABELS[code], "group": DISPOSITIONS[code][1], "count": by_code.get(code, 0)}
            for code in DISPOSITIONS
        ]
        per_day = {r["d"]: r for r in acts.annotate(d=TruncDate("created_at")).values("d").annotate(
            n=Count("id"), c=Count("id", filter=Q(disposition__in=CONNECTED)))}
        daily = []
        for i in range(min(days, 30)):
            d = today - timedelta(days=min(days, 30) - 1 - i)
            daily.append({"date": d, "calls": per_day.get(d, {}).get("n", 0), "connected": per_day.get(d, {}).get("c", 0)})
        return Response({
            "period": {"days": days, "from": start.date(), "to": today},
            "portfolio": portfolio(),
            "calls": {"total": total, "connected": connected, "contact_rate": _rate(connected, total),
                      "accounts_worked": acts.values("loan").distinct().count()},
            "outcomes": outcomes,
            "daily": daily,
            "promises": promises(start, today),
            "agents": agent_rows(start, today),
        })
