"""Remove messages that were saved as customer replies but were really messages sent FROM our number (by the CRM, by Hello, or by other
apps on the same MSG91 number). MSG91 reports each of those with eventName and direction "1"; before the fix they were stored as
inbound text. Reads the webhook bodies still in the log (the last 200) and finds the saved rows by message id. Shows what it would
delete; add --apply to delete."""
import json

from django.core.management.base import BaseCommand

from messaging.models import WhatsappMessage, WhatsappWebhookLog
from messaging.whatsapp import is_outbound_event, refs_of


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="delete them (default: only count)")

    def handle(self, *args, **opts):
        refs = set()
        for row in WhatsappWebhookLog.objects.all():
            try:
                body = json.loads(row.body)
            except ValueError:
                continue
            if is_outbound_event(body) and not body.get("dryRun"):
                refs.update(refs_of(body))
        rows = WhatsappMessage.objects.filter(direction="in", provider_ref__in=refs)
        n = rows.count()
        self.stdout.write(f"{n} saved customer messages are really messages sent from our number (of {len(refs)} such events in the log).")
        if opts["apply"]:
            rows.delete()
            self.stdout.write(f"Deleted {n}.")
        elif n:
            self.stdout.write("Run again with --apply to delete them.")
