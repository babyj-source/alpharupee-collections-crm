from django.conf import settings
from django.db import migrations, models

SECTIONS = [
    ("opening", "Opening", 1, "PLACEHOLDER: replace with the approved opening. Greet the customer by name, say who you are and that you are calling from AlphaRupee about the loan account, and confirm you are speaking to the right person before discussing any amount."),
    ("objections", "Handling objections", 2, "PLACEHOLDER: replace with the approved replies to common reasons for not paying (salary delayed, cash-flow, dispute, forgot the due date)."),
    ("payment", "Payment options", 3, "PLACEHOLDER: replace with the payment options to offer: pay the overdue amount today by the payment link, part payment, or a dated promise. Always confirm the amount and the date."),
    ("dos_donts", "Do and don't", 4, "PLACEHOLDER: replace with the approved conduct rules (calling hours, no threats, no calls to references about the debt amount, record the outcome after every call)."),
]


def seed(apps, schema_editor):
    S = apps.get_model("loans", "ScriptSection")
    for key, title, sort, body in SECTIONS:
        S.objects.get_or_create(key=key, defaults={"title": title, "sort": sort, "body": body})


class Migration(migrations.Migration):
    dependencies = [("loans", "0007_activity_message_kind"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="PriorityConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("values", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ScriptSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=30, unique=True)),
                ("title", models.CharField(max_length=60)),
                ("body", models.TextField(blank=True)),
                ("sort", models.PositiveSmallIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["sort", "id"]},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
