"""Rules from the prototype: dispositions, promise-to-pay reasons and limits."""
from .models import CrmStatus

PTP_MAX_DAYS = 7  # a promise may be at most a week out (prototype rule)

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
