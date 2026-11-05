from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import audit
from accounts.permissions import IsAdmin, IsHead

from .models import SmsTemplate


class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SmsTemplate
        fields = ["key", "name", "msg91_template_id", "kind", "message_type", "version", "body", "variables", "dpd_min", "dpd_max", "active"]
        read_only_fields = ["key", "name", "msg91_template_id", "kind", "message_type", "version", "body", "variables"]

    def validate(self, attrs):
        low, high = attrs.get("dpd_min", self.instance.dpd_min), attrs.get("dpd_max", self.instance.dpd_max)
        if low is not None and low < 0:
            raise serializers.ValidationError("Days past due cannot be negative.")
        if low is not None and high is not None and high < low:
            raise serializers.ValidationError("The upper limit cannot be below the lower limit.")
        return attrs


class TemplateListView(APIView):
    """Head or Admin: the SMS templates and the days-past-due range each one is used for."""

    permission_classes = [IsHead]

    def get(self, request):
        return Response(TemplateSerializer(SmsTemplate.objects.all(), many=True).data)


class TemplateUpdateView(APIView):
    """Admin: change a bucket's day range or switch a template off. The approved text and the MSG91 id are not editable here."""

    permission_classes = [IsAdmin]

    def put(self, request, key):
        t = get_object_or_404(SmsTemplate, key=key)
        ser = TemplateSerializer(t, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "sms_template_updated", user=request.user, object_type="sms_template", object_id=t.pk,
              detail=f"{key} dpd {t.dpd_min}-{t.dpd_max} active={t.active}")
        return Response(TemplateSerializer(t).data)
