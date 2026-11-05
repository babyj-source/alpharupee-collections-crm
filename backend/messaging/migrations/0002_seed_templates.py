from django.db import migrations

# Approved MSG91 SMS templates. The ids are MSG91's template ids. `variables` maps MSG91 variable -> loan field.
# DemandRemainder-1 needs the next EMI due date, which the CRM does not hold yet. Its date variable points at
# "next_due_date", a field templates.SOURCES does not know, so the CRM refuses to build that message (MissingValue)
# until the field is wired, rather than sending a wrong date.
TEMPLATES = [
    dict(key="demand", name="DemandRemainder-1", msg91_template_id="6a64b0274fec41f7f80f6403", kind="demand", message_type="NORMAL",
         version="v1.0", sort=0,
         body="Reminder: Dear Customer, Rs.##number## is due on ##var1## for loan ##var2##. Kindly arrange payment, as late payment may attract penal charges as disclosed in the KFS.-CopperHive Technologies",
         variables={"number": "installment_amount", "var1": "next_due_date", "var2": "account_id"}),
    dict(key="bkt0", name="PaymentRemainder-BKT0", msg91_template_id="6ab622427f629adee5014d73", kind="bucket", message_type="UNICODE",
         version="v1.0", sort=1, dpd_min=0, dpd_max=0,
         body="Your loan payment of ₹##number## is overdue as of today. Please pay using the link ##url## . If already paid, please ignore this message or Call +918065075438 - AlphaRupee",
         variables={"number": "installment_amount", "url": "payment_link"}),
    dict(key="bkt1", name="PaymentRemainder-BKT1", msg91_template_id="6ab62394e384b81a5009d1e2", kind="bucket", message_type="UNICODE",
         version="v1.0", sort=2, dpd_min=1, dpd_max=30,
         body="Alpha Rupee: Your loan is overdue by ##number## days. Outstanding ₹##number1##. Please pay immediately. ##url## | +918065075438",
         variables={"number": "dpd", "number1": "closure_amount", "url": "payment_link"}),
    dict(key="bkt2", name="PaymentRemainder-BKT2", msg91_template_id="6ab6233dd16941664d09be52", kind="bucket", message_type="UNICODE",
         version="v1.0", sort=3, dpd_min=31, dpd_max=60,
         body="Alpha Rupee: Your loan is overdue. Outstanding ₹##number## . Please pay immediately or call +918065075438 or pay now: ##url##",
         variables={"number": "closure_amount", "url": "payment_link"}),
    dict(key="bkt3", name="PaymentRemainder-BKT3", msg91_template_id="6ab6205631857c9a860bea02", kind="bucket", message_type="NORMAL",
         version="v1.1", sort=4, dpd_min=61, dpd_max=90,
         body="Alpha Rupee: Your loan account Rs.##number## remains unpaid despite repeated reminders. Please contact us immediately at +918065075438 or pay now: ##url##",
         variables={"number": "closure_amount", "url": "payment_link"}),
    dict(key="npa", name="PaymentRemainder-NPA", msg91_template_id="6ab6227c642c9e8a59037302", kind="npa", message_type="NORMAL",
         version="v1.1", sort=5, dpd_min=91, dpd_max=None,
         body="Alpha Rupee: Your loan account is in the recovery stage. Contact +918065075438 immediately to discuss repayment options or pay now: ##url##",
         variables={"url": "payment_link"}),
]


def seed(apps, schema_editor):
    SmsTemplate = apps.get_model("messaging", "SmsTemplate")
    for t in TEMPLATES:
        SmsTemplate.objects.get_or_create(key=t["key"], defaults=t)


class Migration(migrations.Migration):
    dependencies = [("messaging", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
