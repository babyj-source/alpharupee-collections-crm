from django.conf import settings
from django.db import models


class Campaign(models.Model):
    """One dialler campaign. The six built-in ones (custom=False) have fixed rules in pools.py; the head can add custom ones
    with rules of their own. The Smartflo campaign and list ids are typed in by the head or Admin."""

    key = models.CharField(max_length=40, unique=True)  # built-in: dpd0, dpd1_7, dpd8_15, dpd16, ptp, callback; custom: c_<name>
    custom = models.BooleanField(default=False)
    rules = models.JSONField(default=dict, blank=True, help_text="custom campaigns only: who is eligible")
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
    last_seen_at = models.DateTimeField(null=True, blank=True, help_text="last time the agent's Dialer screen checked in")
    on_call = models.BooleanField(default=False, help_text="as of last_seen_at")

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


class PushedLead(models.Model):
    """A customer sent to a campaign's Smartflo list on a given day, so starting again does not send them twice."""

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
    loan = models.ForeignKey("loans.Loan", on_delete=models.CASCADE, related_name="+")
    pushed_on = models.DateField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campaign", "loan", "pushed_on"], name="uniq_pushed_lead")]
        indexes = [models.Index(fields=["pushed_on", "campaign"])]
