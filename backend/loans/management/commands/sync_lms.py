from django.core.management.base import BaseCommand, CommandError

from loans.lms import LmsNotConfigured, LmsReader
from loans.sync import run_sync


class Command(BaseCommand):
    help = "Copy accounts and dues from the LMS (read-only) into the CRM."

    def handle(self, *a, **o):
        try:
            reader = LmsReader()
        except (LmsNotConfigured, ValueError) as exc:
            raise CommandError(str(exc))
        run = run_sync(reader)
        if not run.ok:
            raise CommandError(run.error)
        self.stdout.write(f"Sync ok: read {run.rows_read}, created {run.created}, updated {run.updated}")
