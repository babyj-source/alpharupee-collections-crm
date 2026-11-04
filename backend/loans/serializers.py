from rest_framework import serializers

from .models import CrmStatus, Loan


class LoanSerializer(serializers.ModelSerializer):
    overdue_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    mobile = serializers.CharField(source="masked_mobile", read_only=True)
    contacts = serializers.SerializerMethodField()
    agent = serializers.SerializerMethodField()

    class Meta:
        model = Loan
        fields = [
            "id", "account_id", "borrower_name", "mobile", "contacts", "product_code", "branch_code",
            "operational_status", "oldest_due_date", "last_repayment_date", "demand_due", "fee_due", "overdue_amount",
            "outstanding", "installment_amount", "dpd", "crm_status", "note", "agent", "synced_at",
        ]
        read_only_fields = fields

    def get_contacts(self, obj):
        return obj.contacts()

    def get_agent(self, obj):
        a = obj.assigned_agent
        return {"id": a.pk, "name": a.name} if a else None


class LoanUpdateSerializer(serializers.Serializer):
    crm_status = serializers.ChoiceField(choices=CrmStatus.choices, required=False)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class AssignSerializer(serializers.Serializer):
    loan_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=500)
    agent_id = serializers.IntegerField(allow_null=True)


class RevealSerializer(serializers.Serializer):
    which = serializers.ChoiceField(choices=["borrower", "alt", "ref1", "ref2"], default="borrower")
