from django.db import migrations, models


class Migration(migrations.Migration):
    """Source moves to encorebootdb.loan_od_account_view; reference contacts added; status becomes text."""

    dependencies = [("loans", "0001_initial")]

    operations = [
        migrations.RenameField("loan", "due_from_date", "oldest_due_date"),
        migrations.RemoveField("loan", "bom_dpd"),
        migrations.RemoveField("loan", "risk_dpd"),
        migrations.AddField("loan", "last_repayment_date", models.DateField(blank=True, null=True)),
        migrations.AddField("loan", "alt_mobile", models.CharField(blank=True, max_length=15)),
        migrations.AddField("loan", "ref1_name", models.CharField(blank=True, max_length=160)),
        migrations.AddField("loan", "ref1_mobile", models.CharField(blank=True, max_length=15)),
        migrations.AddField("loan", "ref1_relation", models.CharField(blank=True, max_length=60)),
        migrations.AddField("loan", "ref2_name", models.CharField(blank=True, max_length=160)),
        migrations.AddField("loan", "ref2_mobile", models.CharField(blank=True, max_length=15)),
        migrations.AddField("loan", "ref2_relation", models.CharField(blank=True, max_length=60)),
        migrations.AlterField(
            "loan",
            "operational_status",
            models.CharField(blank=True, default="", help_text="LMS code, 0 = active", max_length=30),
        ),
        migrations.AlterField(
            "loan",
            "crm_status",
            models.CharField(
                choices=[
                    ("new", "New"), ("contacted", "Contacted"), ("reference_contacted", "Reference contacted"),
                    ("promised", "Promise to pay"), ("not_reachable", "Not reachable"), ("dispute", "Dispute"),
                    ("paid", "Paid"),
                ],
                default="new",
                max_length=20,
            ),
        ),
    ]
