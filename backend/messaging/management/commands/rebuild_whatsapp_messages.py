"""Re-read old customer messages that were saved as a bare "[button]" or "[image]" placeholder, using the webhook bodies still in the
log (the last 200). Shows what it would change; add --apply to change it. --show prints the masked body of each message."""
import json
import re

from django.core.management.base import BaseCommand

from messaging import media
from messaging.models import WhatsappMessage, WhatsappWebhookLog
from messaging.whatsapp import _mask, parse_inbound

PLACEHOLDER = re.compile(r"^\[[\w ]+\]$")


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="save the changes (default: only show them)")
        parser.add_argument("--show", action="store_true", help="print the masked webhook body of each message")

    def handle(self, *args, **o):
        rows = [m for m in WhatsappMessage.objects.filter(direction="in").exclude(provider_ref="") if PLACEHOLDER.match(m.text or "")]
        bodies = list(WhatsappWebhookLog.objects.values_list("body", flat=True))
        fixed = lost = 0
        for m in rows:
            ref = _mask(m.provider_ref)
            body = next((b for b in bodies if ref in b), None)
            if body is None:
                lost += 1
                continue
            if o["show"]:
                self.stdout.write(f"--- message {m.pk} ({m.text}) body:\n{body[:1500]}")
            try:
                kind, text, file = parse_inbound(media.expand(json.loads(body)))
            except ValueError:
                lost += 1
                continue
            if text == m.text and not file:
                self.stdout.write(f"message {m.pk}: still {m.text!r} (the body has nothing more to read)")
                continue
            self.stdout.write(f"message {m.pk}: {m.text!r} -> kind={kind} text={text!r} file={(file or {}).get('type', '-')}")
            fixed += 1
            if o["apply"]:
                m.kind, m.text = kind, text[:2000]
                if file:
                    m.media_type, m.media_name, m.media_mime, m.media_url = file["type"], file["name"], file["mime"], file["url"][:2000]
                m.save()
                if file and file["url"]:
                    media.fetch_inbound(m)
        self.stdout.write(f"{len(rows)} placeholder messages: {fixed} can be read now, {lost} have no webhook body left in the log."
                          + ("" if o["apply"] else " Nothing changed; add --apply to save."))
