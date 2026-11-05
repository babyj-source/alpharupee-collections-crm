"""Rules from the prototype: dispositions, promise-to-pay reasons and limits."""
from .models import CrmStatus

PTP_MAX_DAYS = 7  # a promise may be at most a week out (prototype rule)
CALL_START_HOUR, CALL_END_HOUR = 8, 19  # calling window 08:00-19:00 (prototype rule); call-backs must fall inside it
CALLBACK_STEP_MINUTES = 30
# After these outcomes the borrower was not reached, so a pending call-back stays; any other outcome closes it.
KEEP_CALLBACK = {"no_answer", "not_reachable", "call_back_later"}

# code -> (label, group, CRM status it sets). Groups are the ones used in the prototype's disposition MIS.
DISPOSITIONS = {
    "ptp_given": ("Connected · PTP given", "Promise", CrmStatus.PROMISED),
    "paid_today": ("Connected · paid today", "Paid", CrmStatus.PAID),
    "will_pay_no_date": ("Connected · will pay, no date", "Promise", CrmStatus.PROMISED),
    "refuses": ("Connected · refuses to pay", "Contacted – resisting", CrmStatus.CONTACTED),
    "dispute": ("Connected · dispute / complaint", "Contacted – resisting", CrmStatus.DISPUTE),
    "needs_senior": ("Connected · needs senior agent", "Contacted – resisting", CrmStatus.CONTACTED),
    "reference_contacted": ("Reference contacted", "Contacted", CrmStatus.REFERENCE_CONTACTED),
    "wrong_number": ("Wrong number", "Not contactable", CrmStatus.NOT_REACHABLE),
    "not_reachable": ("Not reachable / switched off", "Not contactable", CrmStatus.NOT_REACHABLE),
    "no_answer": ("No answer", "Not contactable", CrmStatus.NOT_REACHABLE),
    "call_back_later": ("Call back later", "Other", CrmStatus.CONTACTED),
}
DISPOSITION_LABELS = {k: v[0] for k, v in DISPOSITIONS.items()}

PTP_REASONS = {
    "salary_delayed": "Salary delayed",
    "cash_flow": "Cash-flow / business slow",
    "emergency": "Medical or family emergency",
    "forgot": "Forgot the due date",
    "dispute_amount": "Dispute about the amount",
    "unwilling": "Unwilling to pay",
}

# Outcomes where the borrower (or someone for them) was actually spoken to. Used for the contact rate in the MIS.
CONNECTED = {"ptp_given", "paid_today", "will_pay_no_date", "refuses", "dispute", "needs_senior", "reference_contacted"}

# Collection buckets. The same four are the campaign pools for the dialler: (key, label, lowest DPD, highest DPD or None).
BUCKETS = [
    ("dpd0", "0 DPD", 0, 0),
    ("dpd1_7", "1-7 DPD", 1, 7),
    ("dpd8_15", "8-15 DPD", 8, 15),
    ("dpd16", "16+ DPD", 16, None),
]
BUCKET_KEYS = {b[0]: b for b in BUCKETS}


def bucket_key(dpd):
    for key, _label, lo, hi in BUCKETS:
        if dpd >= lo and (hi is None or dpd <= hi):
            return key
    return BUCKETS[0][0]
