import csv

from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Prefetch, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import Role, User, audit
from accounts.permissions import IsHead

from .csvio import clean_text, safe_cell
from .models import ACTIVE_Q, Activity, AllocationBatch, Loan, Ptp, ReferenceContact, SyncRun
from .pagination import Page
from .phones import normalise_mobile
from .serializers import (
    ActivitySerializer, AssignSerializer, DispositionSerializer, LoanSerializer, LoanUpdateSerializer,
    PtpCloseSerializer, PtpSerializer, ReferenceSerializer, RevealSerializer, open_ptp,
)
from .workflow import DISPOSITIONS, DISPOSITION_LABELS, PTP_MAX_DAYS, PTP_REASONS

OVERDUE = ExpressionWrapper(F("demand_due") + F("fee_due"), output_field=DecimalField(max_digits=16, decimal_places=2))

BUCKETS = [("0", 0, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6)]


def scoped(user):
    """Agents see only their own loans, the head sees all. Every loan query goes through this."""
    qs = Loan.objects.select_related("assigned_agent").prefetch_related(
        "refs", Prefetch("ptps", queryset=Ptp.objects.filter(status=Ptp.State.OPEN), to_attr="open_ptps")
    )
    return qs if user.is_head else qs.filter(assigned_agent=user)


def _int_param(p, key):
    try:
        return int(p[key])
    except (TypeError, ValueError):
        raise drf_serializers.ValidationError({key: "Must be a whole number."})


def filter_loans(qs, p, user):
    """The list filters, shared by the list screen and the CSV export so both show the same rows."""
    if p.get("active", "1") == "1":
        qs = qs.filter(ACTIVE_Q)
    if p.get("overdue", "0") == "1":
        qs = qs.filter(dpd__gt=0)
    if p.get("dpd_min"):
        qs = qs.filter(dpd__gte=_int_param(p, "dpd_min"))
    if p.get("dpd_max"):
        qs = qs.filter(dpd__lte=_int_param(p, "dpd_max"))
    if p.get("crm_status"):
        qs = qs.filter(crm_status=p["crm_status"])
    if p.get("disposition"):
        qs = qs.filter(last_disposition=p["disposition"])
    if p.get("agent") == "none":
        qs = qs.filter(assigned_agent__isnull=True)
    elif p.get("agent") and user.is_head:
        qs = qs.filter(assigned_agent_id=_int_param(p, "agent"))
    if p.get("q"):
        q = p["q"].strip()
        qs = qs.filter(Q(account_id__icontains=q) | Q(borrower_name__icontains=q))
    return qs.order_by("-dpd", "account_id")


class LoanListView(APIView):
    def get(self, request):
        qs = filter_loans(scoped(request.user), request.query_params, request.user)
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
        events = []
        if "crm_status" in d and d["crm_status"] != loan.crm_status:
            loan.crm_status, loan.status_changed_at = d["crm_status"], timezone.now()
            events.append(("status", f"Status set to {loan.get_crm_status_display()}"))
        if "note" in d and d["note"] != loan.note:
            loan.note = d["note"]
            events.append(("note", "Note updated"))
        loan.save(update_fields=["crm_status", "note", "status_changed_at"])
        for kind, detail in events:
            Activity.objects.create(loan=loan, user=request.user, kind=kind, detail=detail)
        audit(request, "loan_updated", user=request.user, object_type="loan", object_id=loan.pk, detail=loan.crm_status)
        return Response(LoanSerializer(get_object_or_404(scoped(request.user), pk=pk)).data)


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

    @transaction.atomic
    def post(self, request):
        ser = AssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        agent = None
        if ser.validated_data["agent_id"] is not None:
            agent = get_object_or_404(User, pk=ser.validated_data["agent_id"], role=Role.AGENT, is_active=True)
        loans = list(Loan.objects.filter(pk__in=ser.validated_data["loan_ids"]))
        moving = [l for l in loans if l.assigned_agent_id != (agent.pk if agent else None)]
        Loan.objects.filter(pk__in=[l.pk for l in moving]).update(assigned_agent=agent)
        who = agent.name if agent else "nobody"
        Activity.objects.bulk_create(
            [Activity(loan=l, user=request.user, kind=Activity.Kind.ALLOCATION, detail=f"Allocated to {who}") for l in moving]
        )
        AllocationBatch.objects.create(by=request.user, source="tick", rows=len(loans), moved=len(moving),
                                       same=len(loans) - len(moving))
        audit(request, "loans_assigned", user=request.user, detail=f"{len(moving)} loans to {agent.email if agent else 'nobody'}")
        return Response({"updated": len(moving)})


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
        today = timezone.localdate()
        open_ptps = Ptp.objects.filter(status=Ptp.State.OPEN, loan__in=scoped(request.user).values("pk"))
        def ptp_sum(q):
            agg = q.aggregate(n=Count("id"), amount=Sum("amount"))
            return {"count": agg["n"], "amount": agg["amount"] or 0}
        out = {
            "ptp": {
                "open": ptp_sum(open_ptps),
                "today": ptp_sum(open_ptps.filter(promised_date=today)),
                "lapsed": ptp_sum(open_ptps.filter(promised_date__lt=today)),
            },
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


class DispositionView(APIView):
    """Record the outcome of a call. Sets the CRM status, keeps the promise to pay and writes the timeline."""

    @transaction.atomic
    def post(self, request, pk):
        get_object_or_404(scoped(request.user), pk=pk)  # visibility check: an agent only reaches own accounts
        ser = DispositionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        loan = Loan.objects.select_for_update().get(pk=pk)
        if loan.operational_status.strip().lower() not in ("0", "active"):
            return Response({"detail": "This account is closed."}, status=400)
        label, _group, new_status = DISPOSITIONS[d["code"]]
        now = timezone.now()
        detail = d["note"]
        if d["code"] == "ptp_given":
            amount = d.get("ptp_amount") or loan.overdue_amount
            if amount <= 0:
                return Response({"detail": "Nothing is overdue on this account, so there is nothing to promise."}, status=400)
            Ptp.objects.filter(loan=loan, status=Ptp.State.OPEN).update(status=Ptp.State.SUPERSEDED, closed_at=now)
            Ptp.objects.create(loan=loan, agent=request.user, promised_date=d["ptp_date"], amount=amount, reason=d["ptp_reason"])
            detail = (f"Promise: ₹{amount:,.0f} by {d['ptp_date']:%d %b %Y}. " + d["note"]).strip()
        elif d["code"] == "paid_today":
            Ptp.objects.filter(loan=loan, status=Ptp.State.OPEN).update(status=Ptp.State.KEPT, closed_at=now)
        if loan.crm_status != new_status:
            loan.status_changed_at = now
        loan.crm_status, loan.last_disposition, loan.last_disposition_at = new_status, d["code"], now
        loan.save(update_fields=["crm_status", "status_changed_at", "last_disposition", "last_disposition_at"])
        Activity.objects.create(loan=loan, user=request.user, kind=Activity.Kind.DISPOSITION,
                                disposition=d["code"], detail=detail[:300])
        audit(request, "disposition_saved", user=request.user, object_type="loan", object_id=loan.pk, detail=d["code"])
        return Response(LoanSerializer(get_object_or_404(scoped(request.user), pk=pk)).data)


class TimelineView(APIView):
    def get(self, request, pk):
        loan = get_object_or_404(scoped(request.user), pk=pk)
        rows = loan.activities.select_related("user")[:100]
        return Response(ActivitySerializer(rows, many=True).data)


class PtpListView(APIView):
    """Promises on the user's accounts. state: open (default), today, lapsed, closed or all."""

    def get(self, request):
        today = timezone.localdate()
        qs = Ptp.objects.select_related("loan", "loan__assigned_agent").filter(loan__in=scoped(request.user).values("pk"))
        state = request.query_params.get("state", "open")
        open_ = Ptp.State.OPEN
        if state == "open":
            qs = qs.filter(status=open_)
        elif state == "today":
            qs = qs.filter(status=open_, promised_date=today)
        elif state == "lapsed":
            qs = qs.filter(status=open_, promised_date__lt=today)
        elif state == "closed":
            qs = qs.exclude(status=open_)
        elif state != "all":
            raise drf_serializers.ValidationError({"state": "Unknown state."})
        qs = qs.order_by("-promised_date" if state == "closed" else "promised_date", "id")
        page = Page()
        rows = page.paginate_queryset(qs, request)
        return page.get_paginated_response(PtpSerializer(rows, many=True).data)


class PtpDetailView(APIView):
    @transaction.atomic
    def patch(self, request, pk):
        ptp = get_object_or_404(Ptp.objects.select_related("loan"), pk=pk, loan__in=scoped(request.user).values("pk"))
        ser = PtpCloseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if ptp.status != Ptp.State.OPEN:
            return Response({"detail": "This promise is already closed."}, status=400)
        ptp.status, ptp.closed_at = ser.validated_data["status"], timezone.now()
        ptp.save(update_fields=["status", "closed_at"])
        Activity.objects.create(loan=ptp.loan, user=request.user, kind=Activity.Kind.PTP,
                                detail=f"Promise of ₹{ptp.amount:,.0f} for {ptp.promised_date:%d %b %Y} marked {ptp.status}")
        audit(request, "ptp_closed", user=request.user, object_type="ptp", object_id=ptp.pk, detail=ptp.status)
        return Response(PtpSerializer(ptp).data)


class ReferenceView(APIView):
    """Add, change or clear a reference contact (slot 1 or 2) on an account the user may see."""

    def _loan(self, request, pk, slot):
        if slot not in (1, 2):
            raise drf_serializers.ValidationError({"slot": "Slot must be 1 or 2."})
        loan = get_object_or_404(scoped(request.user), pk=pk)
        if loan.operational_status.strip().lower() not in ("0", "active"):
            raise drf_serializers.ValidationError({"detail": "This account is closed."})
        return loan

    @transaction.atomic
    def put(self, request, pk, slot):
        loan = self._loan(request, pk, slot)
        ser = ReferenceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        mobile = normalise_mobile(d["mobile"]) if d["mobile"].strip() else loan.number_for(f"ref{slot}")
        if not mobile:
            raise drf_serializers.ValidationError({"mobile": "Enter a valid 10-digit mobile number."})
        if mobile in (loan.borrower_mobile, loan.alt_mobile):
            raise drf_serializers.ValidationError({"mobile": "A reference must be a different person from the borrower."})
        ReferenceContact.objects.update_or_create(
            loan=loan, slot=slot,
            defaults={"name": clean_text(d["name"], 160), "relation": clean_text(d["relation"], 60),
                      "mobile": mobile, "updated_by": request.user},
        )
        Activity.objects.create(loan=loan, user=request.user, kind=Activity.Kind.REFERENCE, detail=f"Reference {slot} saved")
        audit(request, "reference_saved", user=request.user, object_type="loan", object_id=loan.pk, detail=f"ref{slot}")
        return Response(LoanSerializer(get_object_or_404(scoped(request.user), pk=pk)).data)

    @transaction.atomic
    def delete(self, request, pk, slot):
        loan = self._loan(request, pk, slot)
        ReferenceContact.objects.update_or_create(
            loan=loan, slot=slot, defaults={"name": "", "relation": "", "mobile": "", "updated_by": request.user}
        )
        Activity.objects.create(loan=loan, user=request.user, kind=Activity.Kind.REFERENCE, detail=f"Reference {slot} removed")
        audit(request, "reference_cleared", user=request.user, object_type="loan", object_id=loan.pk, detail=f"ref{slot}")
        return Response(LoanSerializer(get_object_or_404(scoped(request.user), pk=pk)).data)


EXPORT_HEAD = [
    "Account ID", "Borrower", "Mobile (masked)", "Product", "Branch", "Status in LMS", "DPD", "Bucket",
    "Overdue (INR)", "Outstanding (INR)", "Oldest due date", "Last payment date", "CRM status", "Last call outcome",
    "Last call outcome at", "Open promise date", "Open promise amount (INR)", "Assigned agent", "Agent e-mail",
]


def bucket_of(dpd):
    for label, lo, hi in BUCKETS:
        if lo <= dpd <= hi:
            return label
    return ""


class ExportView(APIView):
    """Full loan report as CSV for the head. Same filters as the list; numbers masked; every download is logged."""

    permission_classes = [IsHead]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "export"

    def get(self, request):
        qs = filter_loans(scoped(request.user), request.query_params, request.user)
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="alpharupee_loans_{timezone.localdate():%Y%m%d}.csv"'
        resp["Cache-Control"] = "no-store"
        resp.write("﻿")  # so Excel reads the file as UTF-8
        w = csv.writer(resp)
        w.writerow(EXPORT_HEAD)
        n = 0
        for l in qs.iterator(chunk_size=500):
            p = open_ptp(l)
            a = l.assigned_agent
            w.writerow([
                safe_cell(l.account_id), safe_cell(l.borrower_name), l.masked_mobile, safe_cell(l.product_code),
                safe_cell(l.branch_code), safe_cell(l.operational_status), l.dpd, bucket_of(l.dpd), l.overdue_amount,
                l.outstanding, l.oldest_due_date or "", l.last_repayment_date or "", l.get_crm_status_display(),
                DISPOSITION_LABELS.get(l.last_disposition, ""),
                timezone.localtime(l.last_disposition_at).strftime("%Y-%m-%d %H:%M") if l.last_disposition_at else "",
                p.promised_date if p else "", p.amount if p else "",
                safe_cell(a.name) if a else "", safe_cell(a.email) if a else "",
            ])
            n += 1
        audit(request, "loans_exported", user=request.user, detail=f"{n} rows")
        return resp


class MetaView(APIView):
    """Lists the screens need, so they never keep their own copy of the rules."""

    def get(self, request):

        return Response({
            "today": timezone.localdate(),
            "ptp_max_days": PTP_MAX_DAYS,
            "dispositions": [{"code": k, "label": v[0], "group": v[1]} for k, v in DISPOSITIONS.items()],
            "ptp_reasons": [{"code": k, "label": v} for k, v in PTP_REASONS.items()],
        })
