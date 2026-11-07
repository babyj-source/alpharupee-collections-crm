"""Screens over data the CRM already holds: agent performance, the audit log, and the call script."""
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AuditLog, Role, User, audit
from accounts.permissions import IsAdmin, IsHead

from .mis import PERIODS, _rate, agent_rows, period_start
from .models import Activity, ScriptSection
from .workflow import CONNECTED, DISPOSITION_LABELS, DISPOSITIONS


# ------------------------------------------------------------------------------------------------------------ performance
def _extras(start, ids):
    """Per agent: messages sent, calls placed from the account screen, payment links made and paid, in the period."""
    from messaging.models import MessageLog, WhatsappMessage
    from payments.models import PaymentLink
    sms = {r["sent_by"]: r["n"] for r in MessageLog.objects.filter(created_at__gte=start, sent_by__in=ids).exclude(state="failed")
           .values("sent_by").annotate(n=Count("id"))}
    wa = {r["sent_by"]: r["n"] for r in WhatsappMessage.objects.filter(created_at__gte=start, direction="out", sent_by__in=ids)
          .exclude(state="failed").values("sent_by").annotate(n=Count("id"))}
    placed = {r["user"]: r["n"] for r in Activity.objects.filter(kind=Activity.Kind.CALL, created_at__gte=start, user__in=ids)
              .values("user").annotate(n=Count("id"))}
    links = {r["created_by"]: r for r in PaymentLink.objects.filter(created_at__gte=start, created_by__in=ids).values("created_by")
             .annotate(n=Count("id"), paid=Count("id", filter=Q(state="paid")))}
    return {i: {"sms_sent": sms.get(i, 0), "whatsapp_sent": wa.get(i, 0), "calls_placed": placed.get(i, 0),
                "links_made": links.get(i, {}).get("n", 0), "links_paid": links.get(i, {}).get("paid", 0)} for i in ids}


def _detail(agent, start, today, days):
    acts = Activity.objects.filter(kind=Activity.Kind.DISPOSITION, created_at__gte=start, user=agent)
    by_code = {r["disposition"]: r["n"] for r in acts.values("disposition").annotate(n=Count("id"))}
    per_day = {r["d"]: r for r in acts.annotate(d=TruncDate("created_at")).values("d").annotate(
        n=Count("id"), c=Count("id", filter=Q(disposition__in=CONNECTED)))}
    span = min(days, 30)
    daily = [{"date": today - timedelta(days=span - 1 - i), "calls": per_day.get(today - timedelta(days=span - 1 - i), {}).get("n", 0),
              "connected": per_day.get(today - timedelta(days=span - 1 - i), {}).get("c", 0)} for i in range(span)]
    return {"agent_id": agent.pk, "agent": agent.name, "daily": daily,
            "outcomes": [{"code": c, "label": DISPOSITION_LABELS[c], "count": by_code.get(c, 0)} for c in DISPOSITIONS]}


class PerformanceView(APIView):
    """Head: every agent's numbers (and one agent in detail with ?agent=<id>). An agent sees only their own."""

    def get(self, request):
        days = PERIODS.get(request.query_params.get("days", "7"), 7)
        today = timezone.localdate()
        start = period_start(today, days)
        rows = agent_rows(start, today)
        extras = _extras(start, [r["agent_id"] for r in rows])
        for r in rows:
            r.update(extras[r["agent_id"]])
        me = not request.user.is_head
        if me:
            rows = [r for r in rows if r["agent_id"] == request.user.pk]
        agent = None
        if me:
            agent = request.user
        elif request.query_params.get("agent"):
            try:
                agent = User.objects.filter(pk=int(request.query_params["agent"]), role=Role.AGENT).first()
            except ValueError:
                return Response({"detail": "agent must be a number."}, status=400)
        totals = None
        if not me and rows:
            calls, conn = sum(r["calls"] for r in rows), sum(r["connected"] for r in rows)
            totals = {"calls": calls, "connected": conn, "contact_rate": _rate(conn, calls), "promises": sum(r["promises"] for r in rows),
                      "promise_amount": sum(r["promise_amount"] for r in rows), "promises_kept": sum(r["promises_kept"] for r in rows),
                      "messages": sum(r["sms_sent"] + r["whatsapp_sent"] for r in rows), "links_paid": sum(r["links_paid"] for r in rows)}
        return Response({"period": {"days": days, "from": start.date(), "to": today}, "mine": me, "rows": rows, "totals": totals,
                         "detail": _detail(agent, start, today, days) if agent else None})


# ------------------------------------------------------------------------------------------------------------------ audit
PAGE = 50


class AuditView(APIView):
    """Admin: who did what, newest first. Filter by days, action and a search over person, e-mail and detail. Never holds numbers or passwords."""

    permission_classes = [IsAdmin]

    def get(self, request):
        p = request.query_params
        try:
            days = max(1, min(90, int(p.get("days", 7))))
            page = max(1, int(p.get("page", 1)))
        except ValueError:
            return Response({"detail": "days and page must be numbers."}, status=400)
        qs = AuditLog.objects.filter(created_at__gte=timezone.now() - timedelta(days=days)).select_related("user")
        if p.get("action"):
            qs = qs.filter(action=p["action"])
        q = (p.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(email_tried__icontains=q) | Q(detail__icontains=q) | Q(user__name__icontains=q) | Q(user__email__icontains=q)
                           | Q(object_id=q))
        total = qs.count()
        rows = qs.order_by("-created_at", "-id")[(page - 1) * PAGE: page * PAGE]
        actions = sorted(AuditLog.objects.filter(created_at__gte=timezone.now() - timedelta(days=days)).values_list("action", flat=True).distinct())
        return Response({
            "count": total, "page": page, "page_size": PAGE, "actions": actions,
            "results": [{"id": r.pk, "at": r.created_at, "who": r.user.name if r.user else "", "email": r.user.email if r.user else r.email_tried,
                         "action": r.action, "object": f"{r.object_type} {r.object_id}".strip(), "detail": r.detail, "ip": r.ip} for r in rows],
        })


# ------------------------------------------------------------------------------------------------------------------ script
class ScriptSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=60, required=False)
    body = serializers.CharField(max_length=4000, allow_blank=True, required=False)


class ScriptListView(APIView):
    """Everyone signed in: the call script, in order."""

    def get(self, request):
        return Response([{"key": s.key, "title": s.title, "body": s.body} for s in ScriptSection.objects.all()])


class ScriptUpdateView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, key):
        sec = ScriptSection.objects.filter(key=key).first()
        if sec is None:
            return Response({"detail": "Unknown script section."}, status=404)
        ser = ScriptSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        for k, v in ser.validated_data.items():
            setattr(sec, k, v.strip() if k == "title" else v)
        if not sec.title:
            return Response({"detail": "A section needs a title."}, status=400)
        sec.updated_by = request.user
        sec.save()
        audit(request, "script_changed", user=request.user, object_type="script", object_id=sec.pk, detail=key)
        return Response({"key": sec.key, "title": sec.title, "body": sec.body})
