from django.conf import settings
from django.db import models


class PaymentLink(models.Model):
    """One Easebuzz EasyCollect payment link made for a loan. The amount is the closure amount of the day it was made."""

    class State(models.TextChoices):
        ACTIVE = "active", "Waiting for payment"
        PAID = "paid", "Paid"
        EXPIRED = "expired", "Expired"

    class Lms(models.TextChoices):
        NONE = "", "Not paid"
        PENDING = "pending", "Waiting to be posted to the LMS"
        POSTED = "posted", "Posted to the LMS"
        FAILED = "failed", "Posting to the LMS failed"

    loan = models.ForeignKey("loans.Loan", on_delete=models.CASCADE, related_name="payment_links")
    merchant_txn = models.CharField(max_length=40, unique=True)      # our id for the link at Easebuzz; also the key for the status check
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    closure_as_on = models.DateField(null=True, blank=True)
    payment_url = models.URLField(max_length=300)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    easebuzz_state = models.CharField(max_length=30, blank=True)     # what Easebuzz last said, for support
    expires_on = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    lms_state = models.CharField(max_length=10, choices=Lms.choices, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["state", "created_at"])]

    def __str__(self):
        return self.merchant_txn
