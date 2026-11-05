from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from accounts.models import mask_mobile

# The LMS stores the account status as a code (0 = active, 2 = closed). Text such as "Active" is accepted too.
ACTIVE_Q = Q(operational_status__iexact="active") | Q(operational_status="0")


class CrmStatus(models.TextChoices):
    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    REFERENCE_CONTACTED = "reference_contacted", "Reference contacted"
    PROMISED = "promised", "Promise to pay"
    NOT_REACHABLE = "not_reachable", "Not reachable"
    DISPUTE = "dispute", "Dispute"
    PAID = "paid", "Paid"


# Phone numbers an agent may ask to see. The key is what the API and the audit log use.
CONTACTS = [
    # key, label, number field, name field, relationship field
    ("borrower", "Borrower", "borrower_mobile", "borrower_name", None),
    ("alt", "Borrower (second number)", "alt_mobile", "borrower_name", None),
    ("ref1", "Reference 1", "ref1_mobile", "ref1_name", "ref1_relation"),
    ("ref2", "Reference 2", "ref2_mobile", "ref2_name", "ref2_relation"),
]


class Loan(models.Model):
    """One loan account. LMS-owned fields are overwritten by every sync; CRM-owned fields never are."""

    # ---- LMS-owned (read-only copy of loan_od_account_view, lowest holder_num)
    tenant_code = models.CharField(max_length=20)
    account_id = models.CharField(max_length=40)
    customer_id = models.CharField(max_length=40, blank=True)
    product_code = models.CharField(max_length=40, blank=True)
    branch_code = models.CharField(max_length=40, blank=True)
    operational_status = models.CharField(max_length=30, blank=True, default="", help_text="LMS code, 0 = active")
    oldest_due_date = models.DateField(null=True, blank=True)
    last_repayment_date = models.DateField(null=True, blank=True)
    demand_due = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    fee_due = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=16, decimal_places=2, default=0, help_text="as in the LMS (negative = owed)")
    installment_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    dpd = models.IntegerField(default=0)

    # ---- contact details copied from the LMS for active loans only; masked in the API
    borrower_name = models.CharField(max_length=160, blank=True)
    borrower_mobile = models.CharField(max_length=15, blank=True)
    alt_mobile = models.CharField(max_length=15, blank=True)
    ref1_name = models.CharField(max_length=160, blank=True)
    ref1_mobile = models.CharField(max_length=15, blank=True)
    ref1_relation = models.CharField(max_length=60, blank=True)
    ref2_name = models.CharField(max_length=160, blank=True)
    ref2_mobile = models.CharField(max_length=15, blank=True)
    ref2_relation = models.CharField(max_length=60, blank=True)

    # ---- CRM-owned
    assigned_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="loans"
    )
    crm_status = models.CharField(max_length=20, choices=CrmStatus.choices, default=CrmStatus.NEW)
    note = models.CharField(max_length=500, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    last_disposition = models.CharField(max_length=30, blank=True)
    last_disposition_at = models.DateTimeField(null=True, blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_code", "account_id"], name="uniq_loan_key")]
        indexes = [models.Index(fields=["operational_status", "-dpd"]), models.Index(fields=["assigned_agent"])]

    def __str__(self):
        return f"{self.tenant_code}/{self.account_id}"

    @property
    def overdue_amount(self):
        return (self.demand_due or Decimal(0)) + (self.fee_due or Decimal(0))

    @property
    def outstanding(self):
        return abs(self.balance or Decimal(0))

    @property
    def masked_mobile(self):
        return mask_mobile(self.borrower_mobile)

    def _ref_overrides(self):
        """Reference contacts typed in the CRM win over the LMS copy, so a sync never overwrites them."""
        return {r.slot: r for r in self.refs.all()}

    def _contact_values(self, key, overrides):
        for k, _label, num_f, name_f, rel_f in CONTACTS:
            if k != key:
                continue
            if key in ("ref1", "ref2"):
                o = overrides.get(int(key[-1]))
                if o is not None:
                    return o.mobile, o.name, o.relation, "crm"
            return getattr(self, num_f), getattr(self, name_f), (getattr(self, rel_f) if rel_f else ""), "lms"
        return "", "", "", "lms"

    def contacts(self):
        """Masked contact list for the API. Rows without a number are left out."""
        overrides = self._ref_overrides()
        out = []
        for key, label, *_rest in CONTACTS:
            number, name, relation, source = self._contact_values(key, overrides)
            if not number:
                continue
            out.append({"key": key, "label": label, "name": name, "relation": relation,
                        "mobile": mask_mobile(number), "source": source})
        return out

    def ref_slots(self):
        """Both reference slots, filled or not, so the screen can offer Add / Edit. Numbers stay masked."""
        overrides = self._ref_overrides()
        out = []
        for slot in (1, 2):
            number, name, relation, source = self._contact_values(f"ref{slot}", overrides)
            out.append({"slot": slot, "name": name, "relation": relation, "has_number": bool(number),
                        "mobile": mask_mobile(number), "source": source if number or name else ""})
        return out

    def number_for(self, key):
        number, *_ = self._contact_values(key, self._ref_overrides())
        return number


class Activity(models.Model):
    """Timeline of what happened on an account. Never holds phone numbers."""

    class Kind(models.TextChoices):
        DISPOSITION = "disposition", "Call outcome"
        STATUS = "status", "Status"
        NOTE = "note", "Note"
        ALLOCATION = "allocation", "Allocation"
        REFERENCE = "reference", "Reference contact"
        PTP = "ptp", "Promise to pay"

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="activities")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    disposition = models.CharField(max_length=30, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["loan", "-created_at"])]


class Ptp(models.Model):
    """A promise to pay given by the borrower. At most one is open per loan; a new one replaces it."""

    class State(models.TextChoices):
        OPEN = "open", "Open"
        KEPT = "kept", "Kept"
        BROKEN = "broken", "Broken"
        CANCELLED = "cancelled", "Cancelled"
        SUPERSEDED = "superseded", "Replaced"

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="ptps")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    promised_date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    reason = models.CharField(max_length=30)
    status = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["promised_date", "id"]
        indexes = [models.Index(fields=["status", "promised_date"])]


class ReferenceContact(models.Model):
    """CRM-owned reference contact (slot 1 or 2). When present it replaces the LMS value for that slot.
    A blank row means 'cleared in the CRM'."""

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="refs")
    slot = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=160, blank=True)
    mobile = models.CharField(max_length=15, blank=True)
    relation = models.CharField(max_length=60, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["loan", "slot"], name="uniq_ref_slot"),
            models.CheckConstraint(condition=models.Q(slot__in=[1, 2]), name="ref_slot_1_or_2"),
        ]


class AllocationBatch(models.Model):
    """One bulk allocation or tick-box assignment, for the history screen."""

    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source = models.CharField(max_length=10)  # "csv" or "tick"
    filename = models.CharField(max_length=120, blank=True)
    rows = models.IntegerField(default=0)
    moved = models.IntegerField(default=0)
    same = models.IntegerField(default=0)
    errors = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class SyncRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    rows_read = models.IntegerField(default=0)
    created = models.IntegerField(default=0)
    updated = models.IntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-started_at"]
