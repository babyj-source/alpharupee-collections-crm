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

    def contacts(self):
        """Masked contact list for the API. Rows without a number are left out."""
        out = []
        for key, label, num_f, name_f, rel_f in CONTACTS:
            if not getattr(self, num_f):
                continue
            out.append({
                "key": key,
                "label": label,
                "name": getattr(self, name_f),
                "relation": getattr(self, rel_f) if rel_f else "",
                "mobile": mask_mobile(getattr(self, num_f)),
            })
        return out

    def number_for(self, key):
        for k, _label, num_f, _n, _r in CONTACTS:
            if k == key:
                return getattr(self, num_f)
        return None


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
