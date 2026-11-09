from django.db import migrations, models

CODES = {
    "ptp_given": "ptp", "paid_today": "paid", "will_pay_no_date": "willp", "refuses": "refus", "dispute": "dispt",
    "needs_senior": "senr", "reference_contacted": "refct", "wrong_number": "wrong", "not_reachable": "unrch",
    "no_answer": "noans", "call_back_later": "cback",
}


def fill(apps, schema_editor):
    Map = apps.get_model("dialer", "DispositionMap")
    for crm, code in CODES.items():
        Map.objects.update_or_create(crm_code=crm, defaults={"smartflo_code": code})


class Migration(migrations.Migration):
    dependencies = [("dialer", "0005_dial_attempt")]
    operations = [
        migrations.AddField(model_name="dispositionmap", name="smartflo_code",
                            field=models.CharField(blank=True, help_text="the outcome's short code in Smartflo (5 letters at most)", max_length=10)),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
