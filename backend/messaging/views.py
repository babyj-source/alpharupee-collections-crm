from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import audit
from accounts.permissions import IsAdmin, IsHead

from loans.views import scoped

from .models import MessageLog, SmsTemplate, WhatsappTemplate
from .service import SendRefused, preview_sms, send_sms_for_loan


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


class WhatsappSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsappTemplate
        fields = ["key", "name", "msg91_template_id", "namespace", "language", "kind", "days_before_due", "body", "variables",
                  "has_image_header", "header_image_url", "active"]
        read_only_fields = [f for f in fields if f not in ("header_image_url", "active")]

    def validate_header_image_url(self, v):
        if v and not v.startswith("https://"):
            raise serializers.ValidationError("Use a public https:// link.")
        return v


class WhatsappListView(APIView):
    permission_classes = [IsHead]

    def get(self, request):
        return Response(WhatsappSerializer(WhatsappTemplate.objects.all(), many=True).data)


class WhatsappUpdateView(APIView):
    """Admin: set the header image link and switch a template on or off. The approved text and ids are fixed."""

    permission_classes = [IsAdmin]

    def put(self, request, key):
        t = get_object_or_404(WhatsappTemplate, key=key)
        ser = WhatsappSerializer(t, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "whatsapp_template_updated", user=request.user, object_type="whatsapp_template", object_id=t.pk,
              detail=f"{key} active={t.active} image={'set' if t.header_image_url else 'none'}")
        return Response(WhatsappSerializer(t).data)


def _log_row(m):
    return {"id": m.pk, "channel": m.channel, "template": m.template_key, "state": m.state, "text": m.text,
            "error": m.error, "sent_by": m.sent_by.name if m.sent_by else "", "at": m.created_at, "to": "xxxxxx" + m.mobile_last4}


class SendSmsView(APIView):
    """Send the right SMS for a loan. Agents can only do this for their own accounts and cannot choose the template or the link
    (the template follows days past due; the link is the CRM's own). Head and Admin may pick a template or give a link."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "sms"

    def post(self, request):
        try:
            loan_id = int(request.data.get("loan"))
        except (TypeError, ValueError):
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        loan = get_object_or_404(scoped(request.user, grants=True), pk=loan_id)
        key = link = None
        if request.user.is_head:
            key = (request.data.get("template") or "").strip() or None
            link = (request.data.get("link") or "").strip() or None
        try:
            m = send_sms_for_loan(loan, request.user, template_key=key, link=link, again=request.data.get("again") is True)
        except SendRefused as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit(request, "sms_sent" if m.state == "sent" else "sms_failed", user=request.user, object_type="loan", object_id=loan.pk,
              detail=f"{m.template_key} {m.error}".strip())
        if m.state != "sent":
            return Response({"detail": f"MSG91 did not accept the message. {m.error}", "message": _log_row(m)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(_log_row(m), status=status.HTTP_201_CREATED)


class MessageLogView(APIView):
    """Messages sent for one loan, newest first (only loans the user may see)."""

    def get(self, request):
        try:
            loan_id = int(request.query_params.get("loan"))
        except (TypeError, ValueError):
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        loan = get_object_or_404(scoped(request.user, grants=True), pk=loan_id)
        rows = MessageLog.objects.filter(loan=loan).select_related("sent_by")[:30]
        return Response([_log_row(m) for m in rows])


class PreviewView(APIView):
    """The message an agent is about to send: which template and the exact text, or why it cannot go. Nothing is sent."""

    def get(self, request):
        try:
            loan_id = int(request.query_params.get("loan"))
        except (TypeError, ValueError):
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        loan = get_object_or_404(scoped(request.user, grants=True), pk=loan_id)
        return Response(preview_sms(loan))
