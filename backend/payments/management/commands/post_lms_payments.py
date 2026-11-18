import json

from django.core.management.base import BaseCommand

from payments import lms
from payments.models import PaymentLink
from payments.service import LinkRefused, easebuzz_id, post_pending, post_to_lms


class Command(BaseCommand):
    help = ("Post paid payment links to the LMS as repayments. With no options: the same as the one-minute check does. "
            "--link <ref> --dry-run shows exactly what would be sent for one link (no password in it) without sending. "
            "--link <ref> --send posts that one link now (an Admin's test).")

    def add_arguments(self, parser):
        parser.add_argument("--link", help="the link reference (merchant_txn), e.g. ARC1234...")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--send", action="store_true", help="with --link: post it now even if it failed before (check the LMS first)")
        parser.add_argument("--status", action="store_true", help="show the settings that are missing and how many links wait")

    def handle(self, *args, link=None, dry_run=False, send=False, status=False, **opts):
        if status or not (link or dry_run):
            why = lms.missing()
            self.stdout.write(why or "Posting to the LMS is ready.")
            for state in ("pending", "posting", "posted", "failed"):
                self.stdout.write(f"  {state}: {PaymentLink.objects.filter(lms_state=state).count()}")
            if status:
                return
        if link:
            obj = PaymentLink.objects.select_related("loan").filter(merchant_txn=link).first()
            if not obj:
                self.stderr.write("No such link.")
                return
            if dry_run:
                easebuzz_id(obj)                      # fetch it from Easebuzz if the link does not have it yet
                self.stdout.write(json.dumps(lms.build_body(obj), indent=2))
                return
            if not send:
                self.stderr.write("Add --dry-run to see the request, or --send to post it.")
                return
            try:
                obj = post_to_lms(obj, again=True)
            except LinkRefused as exc:
                self.stderr.write(str(exc))
                return
            self.stdout.write(f"{obj.merchant_txn}: {obj.get_lms_state_display()} | {obj.lms_note}")
            if obj.lms_reply:
                self.stdout.write("The LMS's whole answer:\n" + obj.lms_reply)
            return
        posted, failed, left = post_pending()
        self.stdout.write(f"posted {posted}, failed {failed}, waiting {left}")
