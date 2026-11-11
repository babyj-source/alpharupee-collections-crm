import hmac
import json
from datetime import timedelta

from django.http import HttpResponse, HttpResponseNotFound

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import audit
from accounts.permissions import IsAdmin, IsHead

from loans.models import Activity
from loans.phones import normalise_mobile
from loans.views import scoped

from . import media
from .models import MessageLog, SmsTemplate, WhatsappMedia, WhatsappMessage, WhatsappTemplate, WhatsappWebhookLog
from .service import SendRefused, preview_sms, send_sms_for_loan
from .whatsapp import handle_webhook, log_webhook, preview_whatsapp, send_chat_attachment, send_chat_text, send_whatsapp_for_loan, session_open


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
                  "has_image_header", "header_image_url", "active", "auto_send"]
        read_only_fields = [f for f in fields if f not in ("header_image_url", "active", "auto_send")]

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
              detail=f"{key} active={t.active} auto={t.auto_send} image={'set' if t.header_image_url else 'none'}")
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


# ---------------------------------------------------------------------------------------------------------------- WhatsApp
def _wa_row(m):
    row = {"id": m.pk, "direction": m.direction, "kind": m.kind, "text": m.text, "state": m.state, "error": m.error,
           "by": (m.sent_by.name if m.sent_by else "") if m.direction == "out" else "Customer", "at": m.created_at}
    if m.media_type:
        # The browser loads the file from /media/<id> with the user's own login; "ready" says whether the CRM holds the bytes yet.
        row["media"] = {"type": m.media_type, "name": m.media_name, "mime": m.media_mime, "label": media.label_for(m.media_type),
                        "url": f"/api/messaging/whatsapp/media/{m.pk}", "ready": bool(m.media_url or m.direction == "out" or m.media_mime)}
    return row


def _loan_arg(request, data=None):
    src = data if data is not None else request.query_params
    try:
        return get_object_or_404(scoped(request.user, grants=True), pk=int(src.get("loan")))
    except (TypeError, ValueError):
        return None


class WhatsappPreviewView(APIView):
    def get(self, request):
        loan = _loan_arg(request)
        if loan is None:
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        key = (request.query_params.get("template") or "").strip() or None
        return Response(preview_whatsapp(loan, key if request.user.is_head else None))


class WhatsappSendView(APIView):
    """Send an approved WhatsApp template for a loan. Agents cannot choose the template or the link (as with SMS)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "whatsapp"

    def post(self, request):
        loan = _loan_arg(request, request.data)
        if loan is None:
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        key = link = None
        if request.user.is_head:
            key = (request.data.get("template") or "").strip() or None
            link = (request.data.get("link") or "").strip() or None
        try:
            m = send_whatsapp_for_loan(loan, request.user, template_key=key, link=link, again=request.data.get("again") is True)
        except SendRefused as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit(request, "whatsapp_sent" if m.state == "sent" else "whatsapp_failed", user=request.user, object_type="loan", object_id=loan.pk,
              detail=f"{m.template_key} {m.error}".strip())
        if m.state != "sent":
            return Response({"detail": f"MSG91 did not accept the message. {m.error}", "message": _wa_row(m)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(_wa_row(m), status=status.HTTP_201_CREATED)


class ChatView(APIView):
    """The WhatsApp conversation on one account. Opening it marks the customer's messages as read. POST sends free text (JSON) or an
    attachment (multipart: loan, file, text = the caption)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "whatsapp"
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        loan = _loan_arg(request)
        if loan is None:
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        WhatsappMessage.objects.filter(loan=loan, direction="in", seen=False).update(seen=True)
        rows = list(WhatsappMessage.objects.filter(loan=loan).select_related("sent_by").order_by("-created_at", "-id")[:100])[::-1]
        ok, last = session_open(loan)
        closes = last.created_at + timedelta(hours=settings.WHATSAPP_SESSION_HOURS) if ok else None
        return Response({"messages": [_wa_row(m) for m in rows], "window_open": ok, "window_closes_at": closes})

    def post(self, request):
        loan = _loan_arg(request, request.data)
        if loan is None:
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        upload = request.FILES.get("file")
        try:
            if upload is not None:
                m = send_chat_attachment(loan, request.user, upload, request.data.get("text"), request.build_absolute_uri("/"))
            else:
                m = send_chat_text(loan, request.user, request.data.get("text"))
        except SendRefused as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit(request, "whatsapp_chat_sent" if m.state == "sent" else "whatsapp_chat_failed", user=request.user, object_type="loan", object_id=loan.pk,
              detail=f"{m.media_type}: {m.media_name}" if m.media_type else "")
        if m.state != "sent":
            return Response({"detail": f"MSG91 did not accept the message. {m.error}", "message": _wa_row(m)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(_wa_row(m), status=status.HTTP_201_CREATED)


class InboxView(APIView):
    """Conversations on the user's accounts, newest first, with how many customer messages are unread."""

    def get(self, request):
        msgs = (WhatsappMessage.objects.filter(loan__in=scoped(request.user).values("pk"))
                .select_related("loan").order_by("-created_at", "-id")[:600])
        seen, out = {}, []
        for m in msgs:
            row = seen.get(m.loan_id)
            if row is None:
                row = seen[m.loan_id] = {"loan_id": m.loan_id, "account_id": m.loan.account_id, "borrower_name": m.loan.borrower_name,
                                         "last_text": (m.text or (media.label_for(m.media_type) if m.media_type else ""))[:120], "last_direction": m.direction, "last_at": m.created_at, "unread": 0}
                out.append(row)
            if m.direction == "in" and not m.seen:
                row["unread"] += 1
        return Response(out[:100])


def _file_response(m):
    """The stored file of one message. Shown in the page only when it is a kind the browser cannot run; anything else downloads."""
    row = WhatsappMedia.objects.filter(message=m).first()
    if row is None and m.direction == "in":
        try:
            media.fetch_inbound(m)
        except Exception:
            pass
        row = WhatsappMedia.objects.filter(message=m).first()
    if row is None:
        return None
    mime = (m.media_mime or "application/octet-stream").lower()
    inline = mime in media.INLINE_OK
    resp = HttpResponse(bytes(row.content), content_type=mime if inline else "application/octet-stream")
    name = media.clean_name(m.media_name or "file")
    resp["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{name}"'
    if mime != "application/pdf":       # the browser's PDF viewer does not run inside a sandbox; nothing else here can run anything anyway
        resp["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; media-src 'self'"
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


class WhatsappMediaView(APIView):
    """A photo or document in a conversation, for the signed-in user who can see that account."""

    def get(self, request, pk):
        m = WhatsappMessage.objects.filter(pk=pk).exclude(media_type="").first()
        if m is None or m.loan_id is None:
            return HttpResponseNotFound()
        if not scoped(request.user, grants=True).filter(pk=m.loan_id).exists():
            return HttpResponseNotFound()
        return _file_response(m) or HttpResponseNotFound()


class WhatsappMediaPublicView(APIView):
    """The address MSG91 fetches an outgoing attachment from. No login; the link carries a signature for this one file and expires."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, pk):
        if not media.link_is_valid(pk, request.query_params.get("t", "")):
            return HttpResponseNotFound()
        m = WhatsappMessage.objects.filter(pk=pk, direction="out").exclude(media_type="").first()
        return (m and _file_response(m)) or HttpResponseNotFound()


@method_decorator(csrf_exempt, name="dispatch")
class WhatsappWebhookView(APIView):
    """MSG91 calls this with incoming customer messages and delivery updates. No login; a secret token in the address protects it."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        want = settings.WHATSAPP_WEBHOOK_TOKEN
        got = request.query_params.get("token", "")
        if not want or not hmac.compare_digest(want.encode(), got.encode()):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        raw = request.body.decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        try:
            handled = handle_webhook(payload)
        except Exception:      # never let one odd payload make MSG91 retry forever; the body is in the log for us to look at
            handled = "error"
        log_webhook(raw, handled)
        return Response({"ok": True})


class WhatsappWebhookLogView(APIView):
    """Admin: the last webhook bodies MSG91 sent (long digit strings masked), to check the real shape."""

    permission_classes = [IsAdmin]

    def get(self, request):
        return Response([{"at": w.created_at, "handled": w.handled, "body": w.body} for w in WhatsappWebhookLog.objects.all()[:30]])


class WhatsappCallSerializer(serializers.Serializer):
    loan_id = serializers.IntegerField()
    which = serializers.ChoiceField(choices=["borrower", "alt", "ref1", "ref2"], default="borrower")


class WhatsappCallView(APIView):
    """The agent's WhatsApp call button. Returns a wa.me link that opens this customer's chat in the agent's own WhatsApp
    (app or WhatsApp Web), where the agent taps the call icon. MSG91 has no endpoint that places a call, so the call itself is
    made by WhatsApp on the agent's side. Same gate as 'Show number': the user must be able to see the loan; it is audited and
    appears in the account timeline."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reveal"

    def post(self, request):
        from dialer.views import in_window
        from loans.workflow import CALL_END_HOUR, CALL_START_HOUR
        ser = WhatsappCallSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if not settings.WHATSAPP_CALL_ENABLED:
            return Response({"detail": "WhatsApp calling is switched off on this server."}, status=status.HTTP_400_BAD_REQUEST)
        if not in_window():
            return Response({"detail": f"Calling is only allowed between {CALL_START_HOUR:02d}:00 and {CALL_END_HOUR:02d}:00."},
                            status=status.HTTP_400_BAD_REQUEST)
        loan = get_object_or_404(scoped(request.user, grants=True), pk=d["loan_id"])
        if loan.operational_status.strip().lower() not in ("0", "active"):
            return Response({"detail": "This account is closed."}, status=status.HTTP_400_BAD_REQUEST)
        number = normalise_mobile(loan.number_for(d["which"]))
        if not number or not (len(number) == 12 and number.startswith("91") and number[2] in "6789"):
            return Response({"detail": "There is no valid mobile number for that contact."}, status=status.HTTP_400_BAD_REQUEST)
        label = {"borrower": "borrower", "alt": "borrower's second number", "ref1": "reference 1", "ref2": "reference 2"}[d["which"]]
        Activity.objects.create(loan=loan, user=request.user, kind=Activity.Kind.CALL, detail=f"WhatsApp call opened to {label}")
        audit(request, "whatsapp_call_opened", user=request.user, object_type="loan", object_id=loan.pk, detail=d["which"])
        return Response({"url": f"https://wa.me/{number}"})


class SentLogView(APIView):
    """Head or Admin: every SMS or WhatsApp the CRM sent, newest first, with its state. ?channel=sms|whatsapp (default whatsapp),
    state, days (default 7), q (account number or borrower name), page. Numbers are never shown, only the last four digits."""

    permission_classes = [IsHead]
    PAGE = 50

    def get(self, request):
        p = request.query_params
        try:
            days = max(1, min(90, int(p.get("days", 7))))
            page = max(1, int(p.get("page", 1)))
        except ValueError:
            return Response({"detail": "days and page must be numbers."}, status=status.HTTP_400_BAD_REQUEST)
        channel = "sms" if p.get("channel") == "sms" else "whatsapp"
        since = timezone.now() - timedelta(days=days)
        if channel == "sms":
            qs = MessageLog.objects.filter(created_at__gte=since)
        else:
            qs = WhatsappMessage.objects.filter(created_at__gte=since, direction="out")
        qs = qs.select_related("loan", "sent_by")
        if p.get("state"):
            qs = qs.filter(state=p["state"])
        q = (p.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(loan__account_id__icontains=q) | Q(loan__borrower_name__icontains=q))
        total, failed = qs.count(), qs.filter(state="failed").count()
        rows = qs.order_by("-created_at", "-id")[(page - 1) * self.PAGE: page * self.PAGE]
        return Response({"channel": channel, "count": total, "failed": failed, "page": page, "page_size": self.PAGE, "results": [
            {"id": m.pk, "at": m.created_at, "account_id": m.loan.account_id if m.loan else "", "borrower_name": m.loan.borrower_name if m.loan else "",
             "template": m.template_key, "state": m.state, "text": m.text, "error": m.error, "to": "xxxxxx" + m.mobile_last4,
             "by": m.sent_by.name if m.sent_by else "Automatic", "loan_id": m.loan_id} for m in rows]})
