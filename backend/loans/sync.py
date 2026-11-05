import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Loan, Ptp, SyncRun

log = logging.getLogger(__name__)

LMS_FIELDS = [
    "customer_id", "product_code", "branch_code", "operational_status", "oldest_due_date", "last_repayment_date",
    "demand_due", "fee_due", "balance", "installment_amount", "dpd",
]
CONTACT_FIELDS = [
    "borrower_name", "borrower_mobile", "alt_mobile",
    "ref1_name", "ref1_mobile", "ref1_relation", "ref2_name", "ref2_mobile", "ref2_relation",
]


def _dec(v):
    return Decimal(str(v)) if v is not None else Decimal(0)


def _int(v):
    return int(v) if v is not None else 0


def _text(v, n):
    return ("" if v is None else str(v)).strip()[:n]  # not `v or ""`: the status code 0 must stay "0"


def _digits(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    if len(d) == 10:
        d = "91" + d
    return d[-15:]


def _is_active(status):
    return str(status).strip().lower() in ("0", "active")


def _full_name(row):
    parts = [_text(row.get(k), 60) for k in ("first_name", "middle_name", "last_name")]
    return " ".join(p for p in parts if p)[:160]


def _contacts(account, customer):
    """Name and numbers for one active loan. Reference fields come from the customer master."""
    c = customer or {}
    return {
        "borrower_name": _full_name(account),
        "borrower_mobile": _digits(c.get("phone1")),
        "alt_mobile": _digits(c.get("phone2")),
        "ref1_name": _text(c.get("reference_name"), 160),
        "ref1_mobile": _digits(c.get("reference_phone")),
        "ref1_relation": _text(c.get("reference_relationship_type"), 60),
        "ref2_name": _text(c.get("reference_name2"), 160),
        "ref2_mobile": _digits(c.get("reference_phone2")),
        "ref2_relation": _text(c.get("reference_relationship_type2"), 60),
    }


def run_sync(reader):
    """Copy accounts, dues and contacts from the LMS into the CRM. Idempotent; never touches CRM-owned fields.

    Contact details are copied for active loans only; closed loans keep what they had and get no new ones."""
    run = SyncRun.objects.create()
    try:
        # The view has one row per holder; the primary holder is the lowest holder_num.
        accounts, rows_read = {}, 0
        for row in reader.accounts():
            rows_read += 1
            key = str(row["account_id"])
            old = accounts.get(key)
            if old is None or _int(row.get("holder_num")) < _int(old.get("holder_num")):
                accounts[key] = row

        wanted = {str(a.get("customer_id")) for a in accounts.values() if _is_active(a.get("operational_status"))}
        customers = {}
        for c in reader.customers():
            cid = str(c["customer_id"])
            if cid in wanted:
                customers[cid] = c

        tenant = settings.LMS_TENANT_CODE
        existing = {l.account_id: l for l in Loan.objects.filter(tenant_code=tenant)}
        now = timezone.now()
        to_create, to_update = [], []

        for aid, acc in accounts.items():
            status = _text(acc.get("operational_status"), 30)
            values = {
                "customer_id": _text(acc.get("customer_id"), 40),
                "product_code": _text(acc.get("product_code"), 40),
                "branch_code": _text(acc.get("branch_code"), 40),
                "operational_status": status,
                "oldest_due_date": acc.get("oldest_due_demand_date"),
                "last_repayment_date": acc.get("last_repayment_date"),
                "demand_due": _dec(acc.get("total_demand_due_magnitude")),
                "fee_due": _dec(acc.get("total_fee_due_magnitude")),
                "balance": _dec(acc.get("balance_magnitude")),
                "installment_amount": _dec(acc.get("installment_amount_magnitude")),
                "dpd": _int(acc.get("dpd")),
            }
            contacts = _contacts(acc, customers.get(str(acc.get("customer_id")))) if _is_active(status) else {}
            loan = existing.get(aid)
            if loan is None:
                loan = Loan(tenant_code=tenant, account_id=aid, synced_at=now, **values, **contacts)
                to_create.append(loan)
            else:
                for k, v in {**values, **contacts}.items():
                    setattr(loan, k, v)
                loan.synced_at = now
                to_update.append(loan)

        with transaction.atomic():
            Loan.objects.bulk_create(to_create, batch_size=500)
            Loan.objects.bulk_update(to_update, LMS_FIELDS + CONTACT_FIELDS + ["synced_at"], batch_size=500)
            # A promise is kept once the LMS shows nothing overdue on the account.
            Ptp.objects.filter(status=Ptp.State.OPEN, loan__demand_due=0, loan__fee_due=0).update(
                status=Ptp.State.KEPT, closed_at=now
            )
        run.ok, run.rows_read = True, rows_read
        run.created, run.updated = len(to_create), len(to_update)
    except Exception as exc:  # recorded so the head can see a failing sync; no credentials in the message
        log.exception("LMS sync failed")
        run.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    run.finished_at = timezone.now()
    run.save()
    return run
