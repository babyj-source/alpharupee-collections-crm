from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("messaging", "0006_whatsapp_messages")]

    operations = [
        migrations.AddField(model_name="whatsapptemplate", name="auto_send", field=models.BooleanField(default=False,
                            help_text="send this message automatically, once a day, to every overdue account (manage.py auto_whatsapp)")),
    ]
