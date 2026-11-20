import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dialer", "0007_dial_attempt_outcome_sent"),
    ]

    operations = [
        migrations.AddField(model_name="dialersession", name="paused_at",
                            field=models.DateTimeField(blank=True, null=True, help_text="the agent paused the campaign (no new customers are dialled) and has not resumed")),
        migrations.AddField(model_name="dialersession", name="pause_reason",
                            field=models.CharField(blank=True, max_length=20, help_text="key of the reason chosen (see PAUSE_REASONS)")),
        migrations.CreateModel(
            name="DialerPause",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(max_length=20)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pauses", to="dialer.dialersession")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["user", "started_at"], name="dialer_dial_user_id_91572a_idx")]},
        ),
    ]
