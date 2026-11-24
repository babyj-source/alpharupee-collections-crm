from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dialer", "0008_pause")]

    operations = [
        migrations.AddField(model_name="pushedlead", name="withdrawn",
                            field=models.BooleanField(default=False, help_text="taken out of the Smartflo list again (the agent left the campaign) and not yet sent back")),
    ]
