from django.conf import settings
from django.db import models


class Campaign(models.Model):
    """One dialler campaign (a shared pool of customers). The Smartflo ids are typed in by the Admin."""

    key = models.CharField(max_length=20, unique=True)  # dpd0, dpd1_7, dpd8_15, dpd16, ptp, callback
    label = models.CharField(max_length=60)
    sort = models.PositiveSmallIntegerField(default=0)
    smartflo_campaign_id = models.CharField(max_length=20, blank=True)
    smartflo_list_id = models.CharField(max_length=20, blank=True, help_text="Smartflo lead list the CRM pushes customers into")
    active = models.BooleanField(default=False, help_text="agents can only start a session on an active campaign")
    last_pushed_at = models.DateTimeField(null=True, blank=True)
    last_pushed_count = models.IntegerField(default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort", "id"]

    @property
    def configured(self):
        return bool(self.smartflo_campaign_id and self.smartflo_list_id)


class DispositionMap(models.Model):
    """CRM call outcome -> Smartflo disposition status id (needed to release the agent for the next call)."""

    crm_code = models.CharField(max_length=30, unique=True)
    smartflo_status_id = models.CharField(max_length=20, blank=True)


class DialerProfile(models.Model):
    """How a CRM agent appears in Smartflo. The Smartflo password is never stored."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dialer_profile")
    smartflo_email = models.EmailField(blank=True)
    agent_number = models.CharField(max_length=20, blank=True, help_text="the agent's Smartflo number, used for click-to-call")


class DialerSession(models.Model):
    """An agent's logged-in dialler session. Holds the agent's 1-hour Smartflo token, encrypted."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="+")
    token_enc = models.TextField()
    token_expires_at = models.DateTimeField()
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_unique_id = models.CharField(max_length=80, blank=True)
    last_loan = models.ForeignKey("loans.Loan", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    released = models.BooleanField(default=True, help_text="Smartflo has been told the outcome of the last call")

    class Meta:
        indexes = [models.Index(fields=["user", "ended_at"])]


class CampaignPush(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="pushes")
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    leads = models.IntegerField(default=0)
    ok = models.BooleanField(default=False)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
