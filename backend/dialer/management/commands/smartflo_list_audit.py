"""Compare a campaign's Smartflo lead list with what the CRM sent today.

All agents of a campaign share one Smartflo list and Smartflo rings whatever is in it, so a customer left there from an earlier day, from a
manual upload in the Smartflo portal or from an agent who is no longer signed in gets rung by whichever agent is free, even a customer
that is not allocated to anyone. Without --remove this only reads and counts (account ids, never phone numbers). With --remove it takes
out of the list the customers that were NOT sent today: nobody loses anything, because an agent's customers are sent again at their next start.

    manage.py smartflo_list_audit dpd0
    manage.py smartflo_list_audit dpd0 --remove
"""
from time import sleep

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from dialer import views
from dialer.models import Campaign, PushedLead
from dialer.smartflo import SmartfloError, walk_dicts
from loans.models import Loan

MAX_PAGES = 60
LIMIT = 100


def read_list(client, list_id):
    """Every lead in the list: [{'lead_id', 'account', 'status'...}]. Pages by last_seen_id while Smartflo says there are more."""
    seen, out, last, pages = set(), [], None, 0
    while pages < MAX_PAGES:
        pages += 1
        reply = client.list_leads(list_id, last_seen_id=last, limit=LIMIT)
        got = 0
        for d in walk_dicts(reply):
            lid = d.get("lead_id") or d.get("id")
            if lid in (None, "") or "field_2" not in d and "field_0" not in d:
                continue
            if str(lid) in seen:
                continue
            seen.add(str(lid))
            got += 1
            out.append({"lead_id": str(lid), "account": str(d.get("field_2", "")).strip(), "calls": d.get("call_count"), "dial_status": d.get("dial_status")})
        top = reply if isinstance(reply, dict) else {}
        nxt = top.get("last_seen_id")
        more = top.get("has_more") or top.get("hasMore")
        if not got or not more or nxt in (None, "") or nxt == last:
            break
        last = nxt
        sleep(0.3)
    return out, pages


class Command(BaseCommand):
    help = "Count what is in a campaign's Smartflo lead list against what the CRM sent today; --remove takes out the ones not sent today."

    def add_arguments(self, parser):
        parser.add_argument("campaign", help="campaign key, e.g. dpd0, dpd1_7")
        parser.add_argument("--remove", action="store_true", help="delete the leads not sent today from the Smartflo list")

    def handle(self, *args, campaign, remove, **opts):
        camp = Campaign.objects.filter(key=campaign).first()
        if camp is None or not camp.smartflo_list_id:
            raise CommandError("Unknown campaign, or it has no Smartflo list id.")
        client = views.get_client()
        leads, pages = read_list(client, camp.smartflo_list_id)
        today = timezone.localdate()
        sent_today = set(PushedLead.objects.filter(campaign=camp, pushed_on=today).values_list("loan__account_id", flat=True))
        loans = {l.account_id: l for l in Loan.objects.filter(account_id__in={x["account"] for x in leads})}
        groups = {"sent today": [], "not sent today, belongs to an agent": [], "not sent today, not allocated to anyone": [],
                  "not sent today, not in the CRM": []}
        for x in leads:
            loan = loans.get(x["account"])
            if x["account"] in sent_today:
                groups["sent today"].append(x)
            elif loan is None:
                groups["not sent today, not in the CRM"].append(x)
            elif loan.assigned_agent_id is None:
                groups["not sent today, not allocated to anyone"].append(x)
            else:
                groups["not sent today, belongs to an agent"].append(x)
        self.stdout.write(f"{camp.key}: {len(leads)} leads in the Smartflo list (read in {pages} page(s)); CRM sent {len(sent_today)} today")
        for name, items in groups.items():
            self.stdout.write(f"  {name}: {len(items)}")
        stale = [x for name, items in groups.items() if name != "sent today" for x in items]
        if not remove or not stale:
            if stale:
                self.stdout.write("Nothing was changed. Run again with --remove to take the not-sent-today leads out of the list.")
            return
        done, failed = 0, 0
        for x in stale:
            try:
                client.delete_lead(x["lead_id"])
                done += 1
            except SmartfloError as exc:
                failed += 1
                self.stdout.write(f"  stopped: {str(exc)[:120]}")
                break
            sleep(0.25)
        self.stdout.write(f"Removed {done} of {len(stale)} from the Smartflo list" + (f"; stopped on an error, run it again" if failed else "."))
