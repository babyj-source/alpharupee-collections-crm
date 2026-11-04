import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Loan, SyncRun

log = logging.getLogger(__name__)

LMS_FIELDS = [
    "customer_id", "product_code", "branch_code", "operational_status", "due_from_date", "demand_due",
    "fee_due", "balance", "installment_amount", "dpd", "risk_dpd", "bom_dpd",
]


def _dec(v):
    return Decimal(str(v)) if v is not None else Decimal(0)


def _int(v):
    return int(v) if v is not None else 0


def _digits(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    if len(d) == 10:
        d = "91" + d
    return d[-15:]


def run_sync(reader):
    """Copy accounts and their latest collection item into the CRM. Idempotent; never touches CRM-owned fields."""
    run = SyncRun.objects.create()
    try:
        # collection_items is ordered by id, so a later row for the same account replaces an earlier one.
        latest = {}
        rows_read = 0
        for item in reader.items():
            rows_read += 1
            latest[str(item["account_id"])] = item

        borrowers = {b["account_id"]: b for b in reader.borrowers() or []}
        tenant = settings.LMS_TENANT_CODE
        existing = {l.account_id: l for l in Loan.objects.filter(tenant_code=tenant)}
        now = timezone.now()
        to_create, to_update = [], []

        for acc in reader.accounts():
            rows_read += 1
            aid = str(acc["account_id"])
            item = latest.get(aid, {})
            values = {
                "customer_id": str(acc.get("customer_id") or ""),
                "product_code": str(acc.get("product_code") or ""),
                "branch_code": str(acc.get("branch_code") or ""),
                "operational_status": _int(acc.get("operational_status")),
                "due_from_date": item.get("due_from_date"),
                "demand_due": _dec(item.get("demand_due")),
                "fee_due": _dec(item.get("fee_due")),
                "balance": _dec(item.get("balance")),
                "installment_amount": _dec(item.get("installment_amount")),
                "dpd": _int(item.get("dpd")),
                "risk_dpd": _int(item.get("risk_dpd")),
                "bom_dpd": _int(item.get("bom_dpd")),
            }
            b = borrowers.get(aid)
            loan = existing.get(aid)
            if loan is None:
                loan = Loan(tenant_code=tenant, account_id=aid, synced_at=now, **values)
                if b:
                    loan.borrower_name = str(b["name"] or "")[:160]
                    loan.borrower_mobile = _digits(b["mobile"])
                to_create.append(loan)
            else:
                for k, v in values.items():
                    setattr(loan, k, v)
                if b:
                    loan.borrower_name = str(b["name"] or "")[:160]
                    loan.borrower_mobile = _digits(b["mobile"])
                loan.synced_at = now
                to_update.append(loan)

        with transaction.atomic():
            Loan.objects.bulk_create(to_create, batch_size=500)
            fields = LMS_FIELDS + ["borrower_name", "borrower_mobile", "synced_at"]
            Loan.objects.bulk_update(to_update, fields, batch_size=500)
        run.ok, run.rows_read = True, rows_read
        run.created, run.updated = len(to_create), len(to_update)
    except Exception as exc:  # recorded so the head can see a failing sync; no credentials in the message
        log.exception("LMS sync failed")
        run.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    run.finished_at = timezone.now()
    run.save()
    return run
