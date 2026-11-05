from datetime import datetime, time, timedelta

from django.utils import timezone
from rest_framework import serializers

from .models import Activity, CrmStatus, Loan, Ptp
from .workflow import CALL_END_HOUR, CALL_START_HOUR, DISPOSITION_LABELS, DISPOSITIONS, PTP_MAX_DAYS, PTP_REASONS


def open_ptp(loan):
    """The open promise, using the prefetched copy when the loan came from scoped()."""
    cached = getattr(loan, "open_ptps", None)
    if cached is not None:
        return cached[0] if cached else None
    return loan.ptps.filter(status=Ptp.State.OPEN).first()


def ptp_state(p):
    """Open promises are 'due today' or 'lapsed' by date; the agent closes them as kept or broken."""
    if p.status != Ptp.State.OPEN:
        return p.status
    today = timezone.localdate()
    return "lapsed" if p.promised_date < today else "today" if p.promised_date == today else "open"


class LoanSerializer(serializers.ModelSerializer):
    overdue_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    mobile = serializers.CharField(source="masked_mobile", read_only=True)
    contacts = serializers.SerializerMethodField()
    ref_slots = serializers.SerializerMethodField()
    agent = serializers.SerializerMethodField()
    last_disposition_label = serializers.SerializerMethodField()
    open_ptp = serializers.SerializerMethodField()

    class Meta:
        model = Loan
        fields = [
            "id", "account_id", "borrower_name", "mobile", "contacts", "ref_slots", "product_code", "branch_code",
            "operational_status", "oldest_due_date", "last_repayment_date", "demand_due", "fee_due", "overdue_amount",
            "outstanding", "installment_amount", "dpd", "crm_status", "note", "agent", "synced_at",
            "last_disposition", "last_disposition_label", "last_disposition_at", "callback_at", "open_ptp",
        ]
        read_only_fields = fields

    def get_contacts(self, obj):
        return obj.contacts()

    def get_ref_slots(self, obj):
        return obj.ref_slots()

    def get_agent(self, obj):
        a = obj.assigned_agent
        return {"id": a.pk, "name": a.name} if a else None

    def get_last_disposition_label(self, obj):
        return DISPOSITION_LABELS.get(obj.last_disposition, "")

    def get_open_ptp(self, obj):
        p = open_ptp(obj)
        return None if p is None else {"id": p.pk, "date": p.promised_date, "amount": p.amount, "state": ptp_state(p)}


class LoanUpdateSerializer(serializers.Serializer):
    crm_status = serializers.ChoiceField(choices=CrmStatus.choices, required=False)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class AssignSerializer(serializers.Serializer):
    loan_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=500)
    agent_id = serializers.IntegerField(allow_null=True)


class RevealSerializer(serializers.Serializer):
    which = serializers.ChoiceField(choices=["borrower", "alt", "ref1", "ref2"], default="borrower")


class DispositionSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=list(DISPOSITIONS))
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    ptp_date = serializers.DateField(required=False)
    ptp_amount = serializers.DecimalField(max_digits=16, decimal_places=2, required=False, min_value=1)
    ptp_reason = serializers.ChoiceField(choices=list(PTP_REASONS), required=False)
    callback_date = serializers.DateField(required=False)
    callback_time = serializers.TimeField(required=False, input_formats=["%H:%M"])

    def validate(self, data):
        today = timezone.localdate()
        if data["code"] == "ptp_given":
            d = data.get("ptp_date")
            if d is None:
                raise serializers.ValidationError({"ptp_date": "Choose the promise date."})
            if d < today or d > today + timedelta(days=PTP_MAX_DAYS):
                raise serializers.ValidationError({"ptp_date": f"The promise date must be within the next {PTP_MAX_DAYS} days."})
            if not data.get("ptp_reason"):
                raise serializers.ValidationError({"ptp_reason": "Choose the reason for the delay."})
        elif data["code"] == "call_back_later":
            d, t = data.get("callback_date"), data.get("callback_time")
            if d is None or t is None:
                raise serializers.ValidationError({"callback_time": "Choose the call-back date and time."})
            if not (time(CALL_START_HOUR) <= t < time(CALL_END_HOUR)):
                raise serializers.ValidationError({"callback_time": f"Call-backs must be between {CALL_START_HOUR:02d}:00 and {CALL_END_HOUR:02d}:00."})
            if d > today + timedelta(days=PTP_MAX_DAYS):
                raise serializers.ValidationError({"callback_date": f"The call-back must be within the next {PTP_MAX_DAYS} days."})
            when = timezone.make_aware(datetime.combine(d, t))
            if when <= timezone.now():
                raise serializers.ValidationError({"callback_time": "That time has already passed. Choose a later time."})
            data["callback_at"] = when
        return data


class PtpSerializer(serializers.ModelSerializer):
    account_id = serializers.CharField(source="loan.account_id", read_only=True)
    loan_id = serializers.IntegerField(read_only=True)
    borrower_name = serializers.CharField(source="loan.borrower_name", read_only=True)
    dpd = serializers.IntegerField(source="loan.dpd", read_only=True)
    overdue_amount = serializers.DecimalField(source="loan.overdue_amount", max_digits=16, decimal_places=2, read_only=True)
    reason_label = serializers.SerializerMethodField()
    state = serializers.SerializerMethodField()
    agent_name = serializers.SerializerMethodField()

    class Meta:
        model = Ptp
        fields = ["id", "loan_id", "account_id", "borrower_name", "dpd", "overdue_amount", "promised_date", "amount",
                  "reason_label", "status", "state", "agent_name", "created_at"]
        read_only_fields = fields

    def get_reason_label(self, obj):
        return PTP_REASONS.get(obj.reason, obj.reason)

    def get_state(self, obj):
        return ptp_state(obj)

    def get_agent_name(self, obj):
        loan_agent = obj.loan.assigned_agent
        return loan_agent.name if loan_agent else ""


class PtpCloseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["kept", "broken", "cancelled"])


class ActivitySerializer(serializers.ModelSerializer):
    by = serializers.SerializerMethodField()
    disposition_label = serializers.SerializerMethodField()

    class Meta:
        model = Activity
        fields = ["id", "kind", "disposition_label", "detail", "by", "created_at"]
        read_only_fields = fields

    def get_by(self, obj):
        return obj.user.name if obj.user else "System"

    def get_disposition_label(self, obj):
        return DISPOSITION_LABELS.get(obj.disposition, "")


class ReferenceSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    relation = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    mobile = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class CsvTextSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2_000_000, trim_whitespace=False)
    filename = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    apply = serializers.BooleanField(required=False, default=False)


def callback_state(loan):
    when = timezone.localtime(loan.callback_at)
    now = timezone.localtime()
    return "due" if when <= now else "today" if when.date() == now.date() else "upcoming"


class CallbackSerializer(serializers.ModelSerializer):
    loan_id = serializers.IntegerField(source="id", read_only=True)
    state = serializers.SerializerMethodField()
    agent_name = serializers.SerializerMethodField()
    last_disposition_label = serializers.SerializerMethodField()
    overdue_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = Loan
        fields = ["loan_id", "account_id", "borrower_name", "dpd", "overdue_amount", "callback_at", "state",
                  "agent_name", "last_disposition_label"]
        read_only_fields = fields

    def get_state(self, obj):
        return callback_state(obj)

    def get_agent_name(self, obj):
        return obj.assigned_agent.name if obj.assigned_agent else ""

    def get_last_disposition_label(self, obj):
        return DISPOSITION_LABELS.get(obj.last_disposition, "")
