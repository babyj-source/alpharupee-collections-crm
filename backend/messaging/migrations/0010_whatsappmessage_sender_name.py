from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("messaging", "0009_dpd0_template")]

    operations = [
        migrations.AddField(
            model_name="whatsappmessage",
            name="sender_name",
            field=models.CharField(blank=True, help_text="inbound with no matching account: the name on the sender's WhatsApp profile", max_length=80),
        ),
    ]
