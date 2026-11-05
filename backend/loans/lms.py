"""Read-only access to the LMS MySQL (encorebootdb only). Explicit column lists: PAN, date of birth,
addresses and e-mail are never selected."""
import ssl
from contextlib import contextmanager

import pymysql
from django.conf import settings

ACCOUNT_COLUMNS = (
    "account_id, customer_id, holder_num, product_code, branch_code, operational_status, dpd, "
    "oldest_due_demand_date, last_repayment_date, total_demand_due_magnitude, total_fee_due_magnitude, "
    "balance_magnitude, installment_amount_magnitude, first_name, middle_name, last_name, "
    "udf_text2, udf_text3, udf_text4"  # Encore loan-account UDFs: reference name, reference mobile, relationship
)
CUSTOMER_COLUMNS = (
    "customer_id, phone1, phone2, reference_name, reference_phone, reference_relationship_type, "
    "reference_name2, reference_phone2, reference_relationship_type2"
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
        read_timeout=120,
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
    """The two reads the sync needs. Tests replace this class with a fake that has the same methods."""

    def __init__(self):
        self.schema = settings.LMS_SCHEMA
        self.tenant = settings.LMS_TENANT_CODE
        if not self.schema.replace("_", "").isalnum():
            raise ValueError("bad LMS_SCHEMA")

    def _rows(self, sql, params=()):
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            for row in cur:
                yield dict(zip(cols, row))

    def accounts(self):
        """One row per account and holder; the sync keeps the lowest holder_num."""
        sql = f"SELECT {ACCOUNT_COLUMNS} FROM `{self.schema}`.loan_od_account_view WHERE tenant_code = %s"
        return self._rows(sql, (self.tenant,))

    def customers(self):
        sql = f"SELECT {CUSTOMER_COLUMNS} FROM `{self.schema}`.customers WHERE tenant_code = %s"
        return self._rows(sql, (self.tenant,))
