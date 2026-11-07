from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments import easebuzz
from payments.models import PaymentLink
from payments.service import refresh


class Command(BaseCommand):
    help = "Ask Easebuzz about every payment link still waiting for payment (made in the last days) and record the ones that were paid."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, days=7, **opts):
        if not easebuzz.enabled():
            self.stdout.write("Payment links are not switched on; nothing to check.")
            return
        links = PaymentLink.objects.filter(state=PaymentLink.State.ACTIVE, created_at__gte=timezone.now() - timedelta(days=days)).select_related("loan")
        checked = changed = failed = 0
        for link in links:
            try:
                checked += 1
                changed += 1 if refresh(link) else 0
            except easebuzz.EasebuzzError as exc:
                failed += 1
                self.stderr.write(f"{link.merchant_txn}: {exc}")
        self.stdout.write(f"checked {checked}, changed {changed}, could not check {failed}")
