import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dialer", "0009_pushed_lead_withdrawn"),
    ]

    operations = [
        migrations.AddField(model_name="campaign", name="per_agent",
                            field=models.BooleanField(default=False, help_text="each agent gets a Smartflo campaign and lead list of their own (made on their first start), so agents never ring each other's customers")),
        migrations.CreateModel(
            name="AgentRoute",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("smartflo_campaign_id", models.CharField(max_length=20)),
                ("smartflo_list_id", models.CharField(max_length=20)),
                ("smartflo_name", models.CharField(help_text="the campaign's name in Smartflo", max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("campaign", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="routes", to="dialer.campaign")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(model_name="agentroute", constraint=models.UniqueConstraint(fields=("campaign", "user"), name="uniq_agent_route")),
    ]
