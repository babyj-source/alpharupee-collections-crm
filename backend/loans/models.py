from decimal import Decimal

from django.conf import settings
from django.db import models

from accounts.models import mask_mobile


class CrmStatus(models.TextChoices):
    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    PROMISED = "promised", "Promise to pay"
    NOT_REACHABLE = "not_reachable", "Not reachable"
    DISPUTE = "dispute", "Dispute"
    PAID = "paid", "Paid"


class Loan(models.Model):
    """One loan account. LMS-owned fields are overwritten by every sync; CRM-owned fields never are."""

    # ---- LMS-owned (read-only copy)
    tenant_code = models.CharField(max_length=20)
    account_id = models.CharField(max_length=40)
    customer_id = models.CharField(max_length=40, blank=True)
    product_code = models.CharField(max_length=40, blank=True)
    branch_code = models.CharField(max_length=40, blank=True)
    operational_status = models.SmallIntegerField(default=0, help_text="0 active, 2 closed, 1 not yet understood")
    due_from_date = models.DateField(null=True, blank=True)
    demand_due = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    fee_due = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=16, decimal_places=2, default=0, help_text="as in the LMS (negative = owed)")
    installment_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    dpd = models.IntegerField(default=0)
    risk_dpd = models.IntegerField(default=0)
    bom_dpd = models.IntegerField(default=0)
    borrower_name = models.CharField(max_length=160, blank=True)
    borrower_mobile = models.CharField(max_length=15, blank=True)

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
