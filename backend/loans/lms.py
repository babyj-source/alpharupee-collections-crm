"""Read-only access to the LMS MySQL. Explicit column lists only: customer identity tables are never selected."""
import ssl
from contextlib import contextmanager

import pymysql
from django.conf import settings

ACCOUNT_COLUMNS = "account_id, customer_id, product_code, branch_code, operational_status"
ITEM_COLUMNS = (
    "id, account_id, due_from_date, demand_due, fee_due, balance, installment_amount, dpd, risk_dpd, bom_dpd"
)


class LmsNotConfigured(Exception):
    pass


def _ssl_context():
    ca = settings.LMS_DB.get("ssl_ca")
    if ca:
        return ssl.create_default_context(cafile=ca)
    # Encrypted but not identity-checked until the LMS certificate chain is provided (LMS_DB_SSL_CA).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@contextmanager
def connect():
    cfg = settings.LMS_DB
    if not (cfg["host"] and cfg["user"] and cfg["database"]):
        raise LmsNotConfigured("LMS_DB_* settings are empty")
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        ssl=_ssl_context(),
        connect_timeout=10,
        read_timeout=60,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.SSCursor,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(f"SET SESSION max_execution_time = {int(settings.LMS_QUERY_TIMEOUT_MS)}")
        yield conn
    finally:
        conn.close()


class LmsReader:
    """The three reads the sync needs. Tests replace this class with a fake that has the same methods."""

    def __init__(self):
        self.schema = settings.LMS_COLLECTIONS_SCHEMA
        if not self.schema.replace("_", "").isalnum():
            raise ValueError("bad LMS_COLLECTIONS_SCHEMA")

    def _rows(self, sql):
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            for row in cur:
                yield dict(zip(cols, row))

    def accounts(self):
        return self._rows(f"SELECT {ACCOUNT_COLUMNS} FROM `{self.schema}`.accounts")

    def items(self):
        return self._rows(f"SELECT {ITEM_COLUMNS} FROM `{self.schema}`.collection_items ORDER BY id")

    def borrowers(self):
        sql = (settings.LMS_BORROWER_SQL or "").strip().rstrip(";")
        if not sql:
            return
        if not sql.lower().startswith("select") or ";" in sql:
            raise ValueError("LMS_BORROWER_SQL must be one SELECT statement")
        for row in self._rows(sql):
            vals = list(row.values())
            yield {"account_id": str(vals[0]), "name": vals[1], "mobile": vals[2]}
