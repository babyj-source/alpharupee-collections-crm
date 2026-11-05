from django.db import migrations

CAMPAIGNS = [
    ("dpd0", "0 DPD"), ("dpd1_7", "1-7 DPD"), ("dpd8_15", "8-15 DPD"), ("dpd16", "16+ DPD"),
    ("ptp", "Promise to pay"), ("callback", "Call-back"),
]
CODES = [
    "ptp_given", "paid_today", "will_pay_no_date", "refuses", "dispute", "needs_senior", "reference_contacted",
    "wrong_number", "not_reachable", "no_answer", "call_back_later",
]


def seed(apps, schema_editor):
    Campaign = apps.get_model("dialer", "Campaign")
    DispositionMap = apps.get_model("dialer", "DispositionMap")
    for i, (key, label) in enumerate(CAMPAIGNS):
        Campaign.objects.get_or_create(key=key, defaults={"label": label, "sort": i})
    for code in CODES:
        DispositionMap.objects.get_or_create(crm_code=code)


class Migration(migrations.Migration):
    dependencies = [("dialer", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
