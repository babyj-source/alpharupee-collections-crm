"""Head's call log from Smartflo's call detail records, with a link to each recording.

Only calls that belong to the CRM are shown: a call to a customer number on a CRM account that an agent placed from the CRM (a
"call placed" entry on the account) or attended and logged an outcome for in the CRM, close to the time of the call. Everything else
Smartflo holds (other teams, calls made outside the CRM) is left out.

Shapes follow the 'Call Detail Records' page of Smartflo's API reference. Fields are read defensively; a record the CRM cannot read is
shown with what could be read. Recording links carry Smartflo's own token, so they are only handed out one at a time, to Head or Admin,
and every play is written to the audit log."""
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import audit
from accounts.permissions import IsHead
from loans.models import ACTIVE_Q, Activity, Loan

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


BEFORE = timedelta(minutes=10)    # the click is logged a little before Smartflo's record starts (it rings the agent first)
AFTER = timedelta(minutes=60)     # the agent may log the outcome some time after the call ended


def _loans_by_number(numbers):
    """{10-digit number: [loans on that number, open and overdue first]}"""
    nums = {n for n in numbers if n}
    if not nums:
        return {}
    q = Q()
    for n in nums:
        q |= Q(borrower_mobile__endswith=n) | Q(alt_mobile__endswith=n)
    out = {n: [] for n in nums}
    for loan in Loan.objects.filter(q).order_by("-id"):
        for n in nums:
            if loan.borrower_mobile.endswith(n) or loan.alt_mobile.endswith(n):
                out[n].append(loan)
    for loans in out.values():
        loans.sort(key=lambda l: (_active(l), (l.dpd or 0) > 0), reverse=True)
    return {n: v for n, v in out.items() if v}


def _started(row):
    """The call's start as an aware datetime, or None."""
    try:
        return timezone.make_aware(datetime.strptime(row["at"], FMT))
    except (ValueError, TypeError):
        return None


def _crm_rows(rows):
    """Keep only the calls the CRM placed or took, and add which account and which CRM user."""
    by_number = _loans_by_number([r["number"] for r in rows])
    times = {id(r): _started(r) for r in rows}
    stamps = [t for t in times.values() if t]
    acts = {}
    ids = {l.pk for loans in by_number.values() for l in loans}
    if stamps and ids:
        qs = (Activity.objects.filter(loan_id__in=ids, kind__in=[Activity.Kind.CALL, Activity.Kind.DISPOSITION],
                                      created_at__gte=min(stamps) - BEFORE, created_at__lte=max(stamps) + AFTER + timedelta(hours=1))
              .select_related("user").order_by("created_at"))
        for a in qs:
            acts.setdefault(a.loan_id, []).append(a)
    kept = []
    for r in rows:
        t = times[id(r)]
        if t is None:
            continue
        try:
            end = t + timedelta(seconds=int(r["duration"] or 0))
        except (TypeError, ValueError):
            end = t
        hit = None
        for loan in by_number.get(r["number"], []):
            hit = next((a for a in acts.get(loan.pk, []) if t - BEFORE <= a.created_at <= end + AFTER), None)
            if hit:
                r["loan_id"], r["account_id"], r["borrower_name"] = loan.pk, loan.account_id, loan.borrower_name
                r["crm_user"] = hit.user.name if hit.user else ""
                r["how"] = "placed from the CRM" if hit.kind == Activity.Kind.CALL else "outcome logged in the CRM"
                break
        if hit:
            kept.append(r)
    return kept


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
        checked = len(rows)
        rows = _crm_rows(rows)
        for r in rows:
            r["to"] = "xxxxxx" + r["number"][-4:] if r["number"] else ""
            del r["number"]
        agent = (p.get("agent") or "").strip().lower()
        if agent:
            rows = [r for r in rows if agent in r["agent"].lower() or agent in r["crm_user"].lower()]
        return Response({"count": count, "checked": checked, "page": page, "page_size": PAGE, "results": rows})


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
        _count, parsed = parse_records({"results": [i for i in items if isinstance(i, dict) and str(i.get("call_id")) == call_id]})
        if not _crm_rows(parsed):
            return v.bad("That call was not placed or taken in the CRM.", 404)
        rec = next((i.get("recording_url") for i in items if isinstance(i, dict) and str(i.get("call_id")) == call_id and i.get("recording_url")), "")
        if not rec or not str(rec).startswith("https://"):
            return v.bad("Smartflo has no recording for this call.", 404)
        audit(request, "recording_played", user=request.user, object_type="call", object_id=call_id[:64])
        return Response({"url": rec})
