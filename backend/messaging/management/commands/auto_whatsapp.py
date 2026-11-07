from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from loans.models import ACTIVE_Q, Loan
from loans.serializers import open_ptp
from messaging import msg91
from messaging.models import WhatsappMessage, WhatsappTemplate
from messaging.service import SendRefused, in_window
from messaging.whatsapp import send_whatsapp_for_loan

STOP_AFTER_FAILURES = 5     # a run of failures means MSG91 or the template is unhappy: stop instead of hammering


class Command(BaseCommand):
    help = ("Send the missed-payment WhatsApp template automatically, once a day, to overdue accounts. Runs only inside calling hours, only for "
            "templates the Admin switched to automatic, and skips paid, disputed and closed accounts, accounts with a promise still to come "
            "or a call-back booked, and anyone already sent a WhatsApp template today.")

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=300, help="most messages in one run")
        parser.add_argument("--dry-run", action="store_true", help="say who would be messaged; send nothing")

    def handle(self, *args, limit=300, dry_run=False, **opts):
        template = WhatsappTemplate.objects.filter(kind="overdue", active=True, auto_send=True).first()
        if template is None:
            self.stdout.write("Automatic WhatsApp is not switched on for any template; nothing to do.")
            return
        if not msg91.whatsapp_enabled():
            self.stdout.write("WhatsApp is switched off on this server; nothing to do.")
            return
        if not in_window():
            self.stdout.write("Outside calling hours; nothing sent.")
            return
        now = timezone.now()
        today = timezone.localdate(now)
        done = set(WhatsappMessage.objects.filter(direction="out", kind="template", created_at__date=today).exclude(state="failed")
                   .values_list("loan_id", flat=True))
        qs = (Loan.objects.filter(ACTIVE_Q, dpd__gte=1).exclude(crm_status__in=["paid", "dispute"]).exclude(pk__in=done)
              .exclude(callback_at__gt=now).order_by("-dpd", "account_id"))
        sent = failed = skipped = streak = 0
        for loan in qs.iterator():
            if sent + failed >= limit or streak >= STOP_AFTER_FAILURES:
                break
            p = open_ptp(loan)
            if p is not None and p.promised_date >= today:
                skipped += 1
                continue
            if dry_run:
                sent += 1
                continue
            try:
                msg = send_whatsapp_for_loan(loan, None, template.key)
            except SendRefused:
                skipped += 1          # no valid number, no link, too many today, and so on
                continue
            if msg.state == "sent":
                sent, streak = sent + 1, 0
            else:
                failed, streak = failed + 1, streak + 1
        verb = "would send" if dry_run else "sent"
        note = " Stopped early after repeated failures; check MSG91." if streak >= STOP_AFTER_FAILURES else ""
        self.stdout.write(f"{verb} {sent}, failed {failed}, skipped {skipped}.{note}")
