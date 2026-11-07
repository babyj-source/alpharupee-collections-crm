from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dialer", "0003_campaign_rules_live_feed")]

    operations = [
        migrations.CreateModel(
            name="SmartfloSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=40, unique=True)),
                ("value", models.CharField(blank=True, max_length=120)),
            ],
        ),
    ]
