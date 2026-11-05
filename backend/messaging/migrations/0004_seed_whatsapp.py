from django.db import migrations

NS = "544dead2_efd4_43d5_a69a_1ec9b1dbeafc"
# {{n}} -> loan field. "Outstanding amount" is the day's closure amount. The due date of the reminders that go out
# before the due date is today + days_before_due (templates.SOURCES "rule_due_date"); the missed-payment one uses the
# loan's oldest due date.
TEMPLATES = [
    dict(key="wa_due2", name="repay_due_in_2_days", msg91_template_id="556471", kind="due2", days_before_due=2, sort=0,
         body="Hi {{1}}, your AlphaRupee loan repayment of ₹{{2}} is due on {{3}} (in 2 days). Pay early to avoid last-minute rush: {{4}} Thank you.",
         variables={"1": "first_name", "2": "installment_amount", "3": "rule_due_date", "4": "payment_link"}),
    dict(key="wa_due1", name="repay_due_tomorrow", msg91_template_id="556469", kind="due1", days_before_due=1, sort=1,
         body="Hi {{1}}, reminder: your AlphaRupee loan repayment of ₹{{2}} is due *tomorrow* ({{3}}). Please pay on time: {{4}} Thank you.",
         variables={"1": "first_name", "2": "installment_amount", "3": "rule_due_date", "4": "payment_link"}),
    dict(key="wa_due0", name="repay_due_today_img", msg91_template_id="556481", kind="due0", days_before_due=0, sort=2, has_image_header=True,
         body="Hi {{1}}, just a reminder — ₹{{2}} is due today ({{3}}). Pay here: {{4}}. Thank you!",
         variables={"1": "first_name", "2": "installment_amount", "3": "rule_due_date", "4": "payment_link"}),
    dict(key="wa_overdue", name="repay_overdue_missed", msg91_template_id="556730", kind="overdue", days_before_due=None, sort=3, has_image_header=True,
         body="Dear {{1}},\nOur records indicate that your AlphaRupee loan repayment due on {{2}} is still pending.\nOutstanding Amount: ₹{{3}}\nWe understand that payments can sometimes be missed. We request you to make your payment today to avoid additional charges.\nPay Now: {{4}}\nIf you are facing any difficulty, please contact us on 916364143009.\nAlphaRupee. Thank you!",
         variables={"1": "first_name", "2": "oldest_due_date", "3": "closure_amount", "4": "payment_link"}),
]


def seed(apps, schema_editor):
    W = apps.get_model("messaging", "WhatsappTemplate")
    for t in TEMPLATES:
        W.objects.get_or_create(key=t["key"], defaults=dict(t, namespace=NS, language="en"))


class Migration(migrations.Migration):
    dependencies = [("messaging", "0003_whatsapptemplate")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
