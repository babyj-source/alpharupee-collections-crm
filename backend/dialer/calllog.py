"""Head's call log from Smartflo's call detail records, with a link to each recording.

Shapes follow the 'Call Detail Records' page of Smartflo's API reference. Fields are read defensively; a record the CRM cannot read is
shown with what could be read. Recording links carry Smartflo's own token, so they are only handed out one at a time, to Head or Admin,
and every play is written to the audit log."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import audit
from accounts.permissions import IsHead
from loans.models import ACTIVE_Q, Loan

from . import views as v
from .smartflo import SmartfloError, last10

PAGE = 50
FMT = "%Y-%m-%d %H:%M:%S"


def parse_records(payload):
    """-> (count, rows). Each row: call_id, at, direction, status, duration, answered, agent, number (10 digits), outcome, campaign, has_recording."""
    d = payload if isinstance(payload, dict) else {}
    items = d.get("results") if isinstance(d.get("results"), list) else (payload if isinstance(payload, list) else [])
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        dc = it.get("dialer_call_details") if isinstance(it.get("dialer_call_details"), dict) else {}
        rows.append({
            "call_id": str(it.get("call_id") or it.get("uuid") or ""),
            "at": f"{it.get('date') or ''} {it.get('time') or ''}".strip(),
            "direction": str(it.get("direction") or ""), "status": str(it.get("status") or ""),
            "duration": it.get("call_duration") or 0, "answered": it.get("answered_seconds") or 0,
            "agent": str(it.get("agent_name") or ""), "number": last10(it.get("client_number") or it.get("caller_id_num")),
            "outcome": str(dc.get("disposition_name") or ""), "campaign": str(dc.get("campaign_name") or ""),
            "has_recording": bool(it.get("recording_url")),
        })
    count = d.get("count") if isinstance(d.get("count"), int) else len(rows)
    return count, rows


def _loans_by_number(numbers):
    nums = {n for n in numbers if n}
    if not nums:
        return {}
    q = Q()
    for n in nums:
        q |= Q(borrower_mobile__endswith=n) | Q(alt_mobile__endswith=n)
    best = {}
    for loan in Loan.objects.filter(q).order_by("-id"):
        rank = (_active(loan), (loan.dpd or 0) > 0)           # an open, overdue account beats a paid-up or closed one on the same number
        for n in nums:
            if (loan.borrower_mobile.endswith(n) or loan.alt_mobile.endswith(n)) and (n not in best or rank > best[n][0]):
                best[n] = (rank, loan)
    return {n: x[1] for n, x in best.items()}


def _active(loan):
    return str(loan.operational_status).strip().lower() in ("0", "active")


class CallLogView(APIView):
    permission_classes = [IsHead]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin"

    def get(self, request):
        if not v.enabled():
            return v.bad("Smartflo is switched off on this server, or no token is set.")
        p = request.query_params
        try:
            days = {"1": 1, "7": 7, "30": 30}.get(p.get("days", "1"), 1)
            page = max(1, int(p.get("page", 1)))
        except ValueError:
            return v.bad("page must be a number.")
        now = timezone.localtime()
        start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        call_type = {"answered": "c", "missed": "m"}.get(p.get("status"))
        direction = p.get("direction") if p.get("direction") in ("inbound", "outbound") else None
        try:
            payload = v.get_client().call_records(start.strftime(FMT), now.strftime(FMT), page=page, limit=PAGE, call_type=call_type, direction=direction)
        except SmartfloError as exc:
            return v.bad(f"Smartflo refused the request. {exc}", 502)
        count, rows = parse_records(payload)
        loans = _loans_by_number([r["number"] for r in rows])
        for r in rows:
            loan = loans.get(r["number"])
            r["loan_id"], r["account_id"], r["borrower_name"] = (loan.pk, loan.account_id, loan.borrower_name) if loan else (None, "", "")
            r["to"] = "xxxxxx" + r["number"][-4:] if r["number"] else ""
            del r["number"]
        agent = (p.get("agent") or "").strip().lower()
        if agent:
            rows = [r for r in rows if agent in r["agent"].lower()]
        return Response({"count": count, "page": page, "page_size": PAGE, "results": rows})


class RecordingView(APIView):
    """Head or Admin: the recording link for one call. Every request is audited."""

    permission_classes = [IsHead]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin"

    def post(self, request):
        call_id = str(request.data.get("call_id") or "").strip()
        if not call_id or len(call_id) > 80:
            return v.bad("Give the call id.")
        if not v.enabled():
            return v.bad("Smartflo is switched off on this server, or no token is set.")
        now = timezone.localtime()
        try:
            payload = v.get_client().call_records((now - timedelta(days=90)).strftime(FMT), now.strftime(FMT), call_id=call_id, limit=1)
        except SmartfloError as exc:
            return v.bad(f"Smartflo refused the request. {exc}", 502)
        d = payload if isinstance(payload, dict) else {}
        items = d.get("results") if isinstance(d.get("results"), list) else []
        rec = next((i.get("recording_url") for i in items if isinstance(i, dict) and str(i.get("call_id")) == call_id and i.get("recording_url")), "")
        if not rec or not str(rec).startswith("https://"):
            return v.bad("Smartflo has no recording for this call.", 404)
        audit(request, "recording_played", user=request.user, object_type="call", object_id=call_id[:64])
        return Response({"url": rec})
