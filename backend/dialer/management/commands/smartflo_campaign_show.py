"""Show how Smartflo stores one dialler campaign: its lead lists, its agents and any per-agent lead-list setting.

Read-only. Smartflo's public pages do not say what format `map_agent_lead_list` takes. Set it once in the Smartflo portal for a test campaign,
run this, and the stored value shows the format. Prints only list / agent / skill settings and ids, never customer data.

    manage.py smartflo_campaign_show dpd16
"""
import json

from django.core.management.base import BaseCommand, CommandError

from dialer import views
from dialer.models import Campaign
from dialer.smartflo import SmartfloError

WANTED = ("list", "agent", "skill", "traversal", "name", "dial_method", "status")


def pick(node, found=None, path=""):
    """Every setting whose name mentions lists, agents or skills, wherever it sits in the reply."""
    found = {} if found is None else found
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            if any(w in str(k).lower() for w in WANTED):
                found[here] = v
            else:
                pick(v, found, here)
    elif isinstance(node, list):
        for i, v in enumerate(node[:50]):
            pick(v, found, f"{path}[{i}]")
    return found


class Command(BaseCommand):
    help = "Read-only: print a campaign's lead-list and agent settings as Smartflo stores them."

    def add_arguments(self, parser):
        parser.add_argument("campaign", help="campaign key, e.g. dpd16")

    def handle(self, *args, campaign, **opts):
        camp = Campaign.objects.filter(key=campaign).first()
        if camp is None or not camp.smartflo_campaign_id:
            raise CommandError("Unknown campaign, or it has no Smartflo campaign id.")
        try:
            reply = views.get_client().get_campaign(camp.smartflo_campaign_id)
        except SmartfloError as exc:
            raise CommandError(f"Smartflo said: {str(exc)[:200]}")
        shown = pick(reply)
        self.stdout.write(f"{camp.key} (Smartflo campaign {camp.smartflo_campaign_id}): {len(shown)} list/agent settings")
        for k, v in shown.items():
            self.stdout.write(f"  {k}: {json.dumps(v, default=str)[:400]}")
