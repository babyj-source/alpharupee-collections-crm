"""Head-only bulk tools: allocation by CSV (with a preview before anything changes) and reference-contact upload."""
import csv

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import Role, User, audit, mask_mobile
from accounts.permissions import IsHead

from .csvio import CsvError, clean_text, read_rows, safe_cell
from .models import ACTIVE_Q, Activity, AllocationBatch, Loan, ReferenceContact
from .phones import normalise_mobile
from .serializers import CsvTextSerializer

PREVIEW_ROWS = 200   # rows sent back for the preview table
MAX_ERROR_ROWS = 500  # error rows sent back so the screen can offer a download

ALLOC_COLUMNS = {
    "loan": {"loan_id", "account_id", "loan", "account", "loan_no", "loan_number"},
    "agent": {"agent", "agent_email", "agent_id", "assign_to", "assigned_agent", "agent_login", "agent_name"},
}
REF_COLUMNS = {
    "account": {"account_id", "loan_id", "account", "loan", "loan_no"},
    "slot": {"slot", "ref", "reference_no", "reference_number", "ref_no"},
    "name": {"ref_name", "reference_name", "name"},
    "mobile": {"ref_mobile", "reference_mobile", "reference_phone", "mobile", "phone"},
    "relation": {"ref_relation", "relation", "relationship", "reference_relationship"},
}
UNASSIGN_WORDS = {"none", "unassigned", "unassign", "nobody"}


class BulkView(APIView):
    permission_classes = [IsHead]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "bulk"

    def parse(self, request):
        ser = CsvTextSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return ser.validated_data

    @staticmethod
    def respond(rows, counts, apply, applied_counts=None, extra=None):
        errors = [r for r in rows if r["status"] == "error"]
        body = {
            "applied": apply,
            "counts": counts,
            "rows": rows[:PREVIEW_ROWS],
            "errors": errors[:MAX_ERROR_ROWS],
        }
        body.update(extra or {})
        return Response(body)


def _agent_lookup():
    agents = list(User.objects.filter(role=Role.AGENT, is_active=True))
    by_email = {a.email.lower(): a for a in agents}
    by_id = {str(a.pk): a for a in agents}
    names = {}
    for a in agents:
        names.setdefault(a.name.strip().lower(), []).append(a)
    return by_email, by_id, names


def _resolve_agent(text, lookup):
    """Return (agent_or_None, unassign_flag, error)."""
    by_email, by_id, names = lookup
    t = (text or "").strip()
    if not t:
        return None, False, "Agent is empty (write 'unassigned' to remove the agent)"
    if t.lower() in UNASSIGN_WORDS:
        return None, True, ""
    a = by_email.get(t.lower()) or by_id.get(t)
    if a:
        return a, False, ""
    named = names.get(t.lower(), [])
    if len(named) == 1:
        return named[0], False, ""
    if len(named) > 1:
        return None, False, f"More than one agent is called “{clean_text(t, 40)}”; use the e-mail"
    return None, False, f"Agent “{clean_text(t, 40)}” not found"


def _load(agent_ids_names, moves):
    """Active overdue accounts per agent before and after the moves. moves: {loan_pk: (old_agent_id, new_agent_id)}."""
    base = {
        r["assigned_agent"]: r["n"]
        for r in Loan.objects.filter(ACTIVE_Q, dpd__gt=0).values("assigned_agent").annotate(n=Count("id"))
    }
    overdue_ids = set(Loan.objects.filter(ACTIVE_Q, dpd__gt=0, pk__in=list(moves)).values_list("pk", flat=True))
    after = dict(base)
    for pk, (old, new) in moves.items():
        if pk in overdue_ids and old != new:
            after[old] = after.get(old, 0) - 1
            after[new] = after.get(new, 0) + 1
    out = [{"agent": name, "before": base.get(aid, 0), "after": after.get(aid, 0)} for aid, name in agent_ids_names]
    return out


class AllocationCsvView(BulkView):
    """POST {text, apply}. apply=false only checks the file and shows what would happen."""

    def post(self, request):
        d = self.parse(request)
        try:
            raw, found = read_rows(d["text"], ALLOC_COLUMNS)
        except CsvError as exc:
            return Response({"detail": str(exc)}, status=400)
        if not {"loan", "agent"} <= found:
            return Response({"detail": "The first row must name the columns: loan_id and agent."}, status=400)
        lookup = _agent_lookup()
        tenant = settings.LMS_TENANT_CODE
        wanted = {r["loan"] for _n, r in raw if r["loan"]}
        loans = {l.account_id: l for l in Loan.objects.filter(tenant_code=tenant, account_id__in=wanted).select_related("assigned_agent")}
        seen, rows, plan = {}, [], []
        for n, r in raw:
            aid = r["loan"]
            row = {"row": n, "loan": clean_text(aid, 40), "agent": clean_text(r["agent"], 80), "status": "error", "message": "", "borrower": ""}
            agent, unassign, err = _resolve_agent(r["agent"], lookup)
            loan = loans.get(aid)
            if not aid:
                row["message"] = "Loan ID is empty"
            elif aid in seen:
                row["message"] = f"Duplicate of row {seen[aid]} in this file"
            else:
                seen[aid] = n
                if loan is None:
                    row["message"] = "Not in the CRM yet (accounts arrive from the LMS every 15 minutes)"
                elif err:
                    row["message"] = err
                elif not _is_active(loan):
                    row["message"] = "Loan is closed and cannot be allocated"
                else:
                    row["borrower"] = loan.borrower_name
                    target = None if unassign else agent
                    cur = loan.assigned_agent
                    if (cur.pk if cur else None) == (target.pk if target else None):
                        row["status"], row["message"] = "same", f"Already with {cur.name if cur else 'nobody'}"
                    else:
                        row["status"] = "move"
                        row["message"] = f"{cur.name if cur else 'Unassigned'} → {target.name if target else 'Unassigned'}"
                        plan.append((loan, target))
            rows.append(row)
        counts = {
            "rows": len(rows),
            "move": sum(r["status"] == "move" for r in rows),
            "same": sum(r["status"] == "same" for r in rows),
            "errors": sum(r["status"] == "error" for r in rows),
        }
        agents = [(a.pk, a.name) for a in sorted(lookup[1].values(), key=lambda a: a.name)] + [(None, "Unassigned")]
        moves = {l.pk: (l.assigned_agent_id, t.pk if t else None) for l, t in plan}
        load = _load(agents, moves)
        if d["apply"]:
            with transaction.atomic():
                by_target = {}
                for loan, target in plan:
                    by_target.setdefault(target.pk if target else None, (target, []))[1].append(loan)
                for target, group in by_target.values():
                    Loan.objects.filter(pk__in=[l.pk for l in group]).update(assigned_agent=target)
                    who = target.name if target else "nobody"
                    Activity.objects.bulk_create([
                        Activity(loan=l, user=request.user, kind=Activity.Kind.ALLOCATION, detail=f"Allocated to {who} (file)")
                        for l in group
                    ])
                AllocationBatch.objects.create(
                    by=request.user, source="csv", filename=clean_text(d["filename"], 120), rows=counts["rows"],
                    moved=counts["move"], same=counts["same"], errors=counts["errors"],
                )
                audit(request, "loans_allocated", user=request.user,
                      detail=f"file: {counts['move']} moved, {counts['errors']} rejected")
        return self.respond(rows, counts, d["apply"], extra={"load": load})


def _is_active(loan):
    return loan.operational_status.strip().lower() in ("0", "active")


class AllocationHistoryView(APIView):
    permission_classes = [IsHead]

    def get(self, request):
        rows = AllocationBatch.objects.select_related("by")[:100]
        return Response([
            {"id": b.pk, "at": b.created_at, "by": b.by.name if b.by else "", "source": b.source, "filename": b.filename,
             "rows": b.rows, "moved": b.moved, "same": b.same, "errors": b.errors}
            for b in rows
        ])


class AllocationTemplateView(APIView):
    """Current allocation of the overdue accounts, to edit in Excel and upload back."""

    permission_classes = [IsHead]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "export"

    def get(self, request):
        qs = Loan.objects.filter(ACTIVE_Q, dpd__gt=0).select_related("assigned_agent").order_by("assigned_agent__name", "-dpd", "account_id")
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="allocation_template.csv"'
        resp["Cache-Control"] = "no-store"
        resp.write("﻿")
        w = csv.writer(resp)
        w.writerow(["loan_id", "agent", "borrower", "current_agent", "dpd", "overdue_inr"])
        n = 0
        for l in qs.iterator(chunk_size=500):
            a = l.assigned_agent
            w.writerow([safe_cell(l.account_id), safe_cell(a.email) if a else "unassigned", safe_cell(l.borrower_name),
                        safe_cell(a.name) if a else "", l.dpd, l.overdue_amount])
            n += 1
        audit(request, "allocation_template_downloaded", user=request.user, detail=f"{n} rows")
        return resp


class ReferenceCsvView(BulkView):
    """POST {text, apply}. One row per reference: account_id, slot (1 or 2), ref_name, ref_mobile, ref_relation."""

    def post(self, request):
        d = self.parse(request)
        try:
            raw, found = read_rows(d["text"], REF_COLUMNS)
        except CsvError as exc:
            return Response({"detail": str(exc)}, status=400)
        if not {"account", "name", "mobile"} <= found:
            return Response({"detail": "The first row must name the columns: account_id, ref_name and ref_mobile (slot and ref_relation are optional)."}, status=400)
        tenant = settings.LMS_TENANT_CODE
        wanted = {r["account"] for _n, r in raw if r["account"]}
        loans = {l.account_id: l for l in Loan.objects.filter(tenant_code=tenant, account_id__in=wanted)}
        seen, rows, plan = {}, [], []
        for n, r in raw:
            aid = r["account"]
            slot_txt = r.get("slot", "").lower().replace("ref", "").strip() or "1"
            row = {"row": n, "loan": clean_text(aid, 40), "slot": slot_txt, "name": clean_text(r["name"], 160),
                   "mobile": "", "status": "error", "message": ""}
            mobile = normalise_mobile(r["mobile"])
            row["mobile"] = mask_mobile(mobile)
            loan = loans.get(aid)
            if not aid:
                row["message"] = "Account ID is empty"
            elif slot_txt not in ("1", "2"):
                row["message"] = "Slot must be 1 or 2"
            elif (aid, slot_txt) in seen:
                row["message"] = f"Duplicate of row {seen[(aid, slot_txt)]} in this file"
            else:
                seen[(aid, slot_txt)] = n
                if loan is None:
                    row["message"] = "Not in the CRM yet"
                elif not _is_active(loan):
                    row["message"] = "Loan is closed"
                elif not row["name"]:
                    row["message"] = "Reference name is empty"
                elif not mobile:
                    row["message"] = "Mobile is not a valid 10-digit number"
                elif mobile in (loan.borrower_mobile, loan.alt_mobile):
                    row["message"] = "Same number as the borrower"
                else:
                    row["status"], row["message"] = "ok", "Will be saved"
                    plan.append((loan, int(slot_txt), row["name"], mobile, clean_text(r.get("relation", ""), 60)))
            rows.append(row)
        counts = {"rows": len(rows), "ok": len(plan), "errors": sum(r["status"] == "error" for r in rows)}
        if d["apply"] and plan:
            with transaction.atomic():
                for loan, slot, name, mobile, relation in plan:
                    ReferenceContact.objects.update_or_create(
                        loan=loan, slot=slot,
                        defaults={"name": name, "mobile": mobile, "relation": relation, "updated_by": request.user},
                    )
                Activity.objects.bulk_create([
                    Activity(loan=loan, user=request.user, kind=Activity.Kind.REFERENCE, detail=f"Reference {slot} loaded from file")
                    for loan, slot, *_ in plan
                ])
                audit(request, "references_uploaded", user=request.user,
                      detail=f"{len(plan)} saved, {counts['errors']} rejected")
        return self.respond(rows, counts, d["apply"])
