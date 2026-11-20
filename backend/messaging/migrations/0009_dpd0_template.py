from django.db import migrations, models

NS = "544dead2_efd4_43d5_a69a_1ec9b1dbeafc"      # the account's WhatsApp namespace, the same as the other templates
KINDS = [("due2", "Due in 2 days"), ("due1", "Due tomorrow"), ("due0", "Due today"), ("dpd0", "DPD 0 (amount due today)"), ("overdue", "Missed payment")]


def seed(apps, schema_editor):
    W = apps.get_model("messaging", "WhatsappTemplate")
    W.objects.get_or_create(key="wa_dpd0", defaults=dict(
        name="dpd0", msg91_template_id="620159", namespace=NS, language="en", kind="dpd0", days_before_due=None, sort=4,
        body="Your loan payment of \u20b9{{1}} is overdue as of today. Please pay using the link {{2}} . "
             "If already paid, please ignore this message or Call +918065075438 - AlphaRupee",
        variables={"1": "installment_amount", "2": "payment_link"}))


class Migration(migrations.Migration):
    dependencies = [("messaging", "0008_whatsapp_media")]
    operations = [
        migrations.AlterField(model_name="whatsapptemplate", name="kind", field=models.CharField(choices=KINDS, max_length=10)),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
