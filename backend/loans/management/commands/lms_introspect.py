from django.core.management.base import BaseCommand

from loans.lms import connect

PATTERNS = ("customer", "holder", "party", "person", "contact", "phone", "mobile", "name")


class Command(BaseCommand):
    help = (
        "Read-only. Lists LMS tables and columns whose names suggest borrower name or phone, "
        "so LMS_BORROWER_SQL can be written. Prints names only, never row data."
    )

    def handle(self, *a, **o):
        like = " OR ".join(["table_name LIKE %s"] * len(PATTERNS) + ["column_name LIKE %s"] * len(PATTERNS))
        args = [f"%{p}%" for p in PATTERNS] * 2
        sql = (
            "SELECT table_schema, table_name, GROUP_CONCAT(column_name ORDER BY ordinal_position) "
            "FROM information_schema.columns WHERE table_schema IN ('encorebootdb','encorepaydb') "
            f"AND ({like}) GROUP BY table_schema, table_name ORDER BY 1,2"
        )
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            for schema, table, cols in cur:
                self.stdout.write(f"{schema}.{table}: {cols}")
