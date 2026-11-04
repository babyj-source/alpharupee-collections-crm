from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, IntegerField, Q, Sum, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import Role, User, audit
from accounts.permissions import IsHead

from .models import ACTIVE_Q, Loan, SyncRun
from .pagination import Page
from .serializers import AssignSerializer, LoanSerializer, LoanUpdateSerializer, RevealSerializer

OVERDUE = ExpressionWrapper(F("demand_due") + F("fee_due"), output_field=DecimalField(max_digits=16, decimal_places=2))

BUCKETS = [("0", 0, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6)]


def scoped(user):
    """Agents see only their own loans, the head sees all. Every loan query goes through this."""
    qs = Loan.objects.select_related("assigned_agent")
    return qs if user.is_head else qs.filter(assigned_agent=user)


class LoanListView(APIView):
    def get(self, request):
        qs = scoped(request.user)
        p = request.query_params
        status_filter = p.get("active", "1")
        if status_filter == "1":
            qs = qs.filter(ACTIVE_Q)
        if p.get("overdue", "0") == "1":
            qs = qs.filter(dpd__gt=0)
        if p.get("dpd_min"):
            qs = qs.filter(dpd__gte=int(p["dpd_min"]))
        if p.get("dpd_max"):
            qs = qs.filter(dpd__lte=int(p["dpd_max"]))
        if p.get("crm_status"):
            qs = qs.filter(crm_status=p["crm_status"])
        if p.get("agent") == "none":
            qs = qs.filter(assigned_agent__isnull=True)
        elif p.get("agent") and request.user.is_head:
            qs = qs.filter(assigned_agent_id=int(p["agent"]))
        if p.get("q"):
            q = p["q"].strip()
            qs = qs.filter(Q(account_id__icontains=q) | Q(borrower_name__icontains=q))
        qs = qs.order_by("-dpd", "account_id")
        page = Page()
        rows = page.paginate_queryset(qs, request)
        return page.get_paginated_response(LoanSerializer(rows, many=True).data)


class LoanDetailView(APIView):
    def get(self, request, pk):
        return Response(LoanSerializer(get_object_or_404(scoped(request.user), pk=pk)).data)

    def patch(self, request, pk):
        loan = get_object_or_404(scoped(request.user), pk=pk)
        ser = LoanUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if "crm_status" in d and d["crm_status"] != loan.crm_status:
            loan.crm_status, loan.status_changed_at = d["crm_status"], timezone.now()
        if "note" in d:
            loan.note = d["note"]
        loan.save(update_fields=["crm_status", "note", "status_changed_at"])
        audit(request, "loan_updated", user=request.user, object_type="loan", object_id=loan.pk, detail=loan.crm_status)
        return Response(LoanSerializer(loan).data)


class RevealMobileView(APIView):
    """Full number only on request, only for loans the user may see, and every request is logged with whose number."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reveal"

    def post(self, request, pk):
        loan = get_object_or_404(scoped(request.user), pk=pk)
        ser = RevealSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        which = ser.validated_data["which"]
        audit(request, "mobile_revealed", user=request.user, object_type="loan", object_id=loan.pk, detail=which)
        return Response({"which": which, "mobile": loan.number_for(which) or ""})


class AssignView(APIView):
    permission_classes = [IsHead]

    def post(self, request):
        ser = AssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        agent = None
        if ser.validated_data["agent_id"] is not None:
            agent = get_object_or_404(User, pk=ser.validated_data["agent_id"], role=Role.AGENT, is_active=True)
        n = Loan.objects.filter(pk__in=ser.validated_data["loan_ids"]).update(assigned_agent=agent)
        audit(request, "loans_assigned", user=request.user, detail=f"{n} loans to {agent.email if agent else 'nobody'}")
        return Response({"updated": n})


class AgentListView(APIView):
    permission_classes = [IsHead]

    def get(self, request):
        agents = User.objects.filter(role=Role.AGENT, is_active=True).order_by("name")
        return Response([{"id": a.pk, "name": a.name, "email": a.email} for a in agents])


class DashboardView(APIView):
    def get(self, request):
        qs = scoped(request.user).filter(ACTIVE_Q)
        total = qs.aggregate(accounts=Count("id"), outstanding=Sum("balance"))
        over = qs.filter(dpd__gt=0)
        over_tot = over.aggregate(accounts=Count("id"), amount=Sum(OVERDUE))
        buckets = []
        for label, lo, hi in BUCKETS:
            agg = qs.filter(dpd__gte=lo, dpd__lte=hi).aggregate(accounts=Count("id"), amount=Sum(OVERDUE))
            buckets.append({"bucket": label, "accounts": agg["accounts"], "amount": agg["amount"] or 0})
        statuses = {r["crm_status"]: r["n"] for r in over.values("crm_status").annotate(n=Count("id"))}
        out = {
            "accounts": total["accounts"],
            "outstanding": abs(total["outstanding"] or 0),
            "overdue_accounts": over_tot["accounts"],
            "overdue_amount": over_tot["amount"] or 0,
            "buckets": buckets,
            "crm_status": statuses,
        }
        if request.user.is_head:
            rows = (
                over.values("assigned_agent", "assigned_agent__name")
                .annotate(accounts=Count("id"), amount=Sum(OVERDUE))
                .order_by("-amount")
            )
            out["by_agent"] = [
                {"agent_id": r["assigned_agent"], "agent": r["assigned_agent__name"] or "Unassigned",
                 "accounts": r["accounts"], "amount": r["amount"] or 0}
                for r in rows
            ]
            last = SyncRun.objects.first()
            out["sync"] = None if last is None else {
                "ok": last.ok, "finished_at": last.finished_at, "created": last.created,
                "updated": last.updated, "error": last.error,
            }
        return Response(out)
