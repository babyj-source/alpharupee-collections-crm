from django.db import models


class SmsTemplate(models.Model):
    """One MSG91 SMS template. The text itself lives in MSG91 / DLT; the copy here is for previews and for knowing
    which loan field fills which variable. `variables` maps each MSG91 variable name to a loan field (see templates.SOURCES)."""

    KIND = [("demand", "Due-date reminder"), ("bucket", "Overdue bucket, with payment link"), ("npa", "NPA, with payment link")]

    key = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=60, help_text="template name in MSG91")
    msg91_template_id = models.CharField(max_length=40)
    kind = models.CharField(max_length=10, choices=KIND)
    message_type = models.CharField(max_length=10, default="NORMAL", help_text="NORMAL or UNICODE (the rupee sign needs UNICODE)")
    version = models.CharField(max_length=10, blank=True)
    body = models.TextField(help_text="approved text with ##variable## markers")
    variables = models.JSONField(default=dict, blank=True, help_text='{"MSG91 variable": "loan field"}')
    dpd_min = models.IntegerField(null=True, blank=True, help_text="bucket templates: lowest days past due")
    dpd_max = models.IntegerField(null=True, blank=True, help_text="bucket templates: highest days past due; empty = no upper limit")
    active = models.BooleanField(default=True)
    sort = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort", "id"]

    def __str__(self):
        return self.name
