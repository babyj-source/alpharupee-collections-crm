from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from loans.lms import LmsReader, connect
from loans.sync import _digits, _int, _is_active


class Command(BaseCommand):
    help = "Read-only. Prints counts only (no names, no numbers) so the LMS assumptions can be checked."

    def handle(self, *a, **o):
        try:
            reader = LmsReader()
        except ValueError as exc:
            raise CommandError(str(exc))
        out = self.stdout.write

        primary, rows = {}, 0
        for r in reader.accounts():
            rows += 1
            k = str(r["account_id"])
            if k not in primary or _int(r.get("holder_num")) < _int(primary[k].get("holder_num")):
                primary[k] = r
        out(f"loan_od_account_view: {rows} rows for {len(primary)} accounts (joint holders: {rows - len(primary)} extra rows)")

        by_status = {}
        for r in primary.values():
            by_status[str(r["operational_status"])] = by_status.get(str(r["operational_status"]), 0) + 1
        out("accounts by operational_status: " + ", ".join(f"{k!r}: {v}" for k, v in sorted(by_status.items())))

        active = [r for r in primary.values() if _is_active(r["operational_status"])]
        late = [r for r in active if _int(r.get("dpd")) > 0]
        overdue = sum((r.get("total_demand_due_magnitude") or 0) + (r.get("total_fee_due_magnitude") or 0) for r in late)
        out(f"active accounts: {len(active)}; with dpd > 0: {len(late)}; max dpd: {max([_int(r.get('dpd')) for r in active] or [0])}")
        out(f"overdue amount (total_demand_due + total_fee_due) on those: {overdue}")
        no_due = [r for r in late if not (r.get("total_demand_due_magnitude") or 0)]
        due_no_dpd = [r for r in active if _int(r.get("dpd")) == 0 and (r.get("total_demand_due_magnitude") or 0) > 0]
        out(f"dpd > 0 but no demand due: {len(no_due)}; dpd = 0 but demand due > 0: {len(due_no_dpd)}")

        s = f"`{settings.LMS_SCHEMA}`"
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {s}.loan_od_account_view WHERE tenant_code = %s AND operational_status = 0 "
                "AND ABS(total_demand_due_magnitude - (total_principal_due_magnitude + total_normal_interest_due_magnitude "
                "+ total_penal_interest_due_magnitude)) > 0.005",
                (settings.LMS_TENANT_CODE,),
            )
            out(f"active rows where total_demand_due differs from principal + interest + penal due: {cur.fetchone()[0]}")

        wanted = {str(r["customer_id"]) for r in active}
        n = with_phone = with_ref = with_ref2 = 0
        for c in reader.customers():
            if str(c["customer_id"]) not in wanted:
                continue
            n += 1
            with_phone += bool(_digits(c.get("phone1")))
            with_ref += bool(_digits(c.get("reference_phone")))
            with_ref2 += bool(_digits(c.get("reference_phone2")))
        out(f"customers of active accounts: {n}; with a mobile: {with_phone}; with reference 1 number: {with_ref}; with reference 2 number: {with_ref2}")
