import base64
import hashlib
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import Role, User, audit
from accounts.permissions import IsAdmin, IsHead
from loans.models import ACTIVE_Q, Activity, CallAccess, Loan
from loans.phones import normalise_mobile
from loans.serializers import LoanSerializer
from loans.views import DispositionView, scoped
from loans.workflow import CALL_END_HOUR, CALL_START_HOUR, DISPOSITION_LABELS, DISPOSITIONS

from .models import Campaign, CampaignPush, DialerProfile, DialerSession, DispositionMap
from .pools import POOL_KEYS, lead_rows, pool_loans
from .smartflo import SmartfloClient, SmartfloDisabled, SmartfloError, last10, parse_live_calls

TOKEN_MINUTES = 55  # Smartflo agent tokens last one hour
ACCESS_HOURS = 3
CHUNK = 500


def get_client():  # a function so tests can swap it
    return SmartfloClient()


def enabled():
    return bool(settings.SMARTFLO_ENABLED and settings.SMARTFLO_API_TOKEN)


def in_window(now=None):
    h = timezone.localtime(now or timezone.now()).hour
    return CALL_START_HOUR <= h < CALL_END_HOUR


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()))


def seal(token):
    return _fernet().encrypt(token.encode()).decode()


def unseal(blob):
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except InvalidToken:
        return ""


def bad(msg, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": msg}, status=code)


def _digits_field(max_len=12):
    return serializers.RegexField(r"^\d*$", max_length=max_len, allow_blank=True, required=False,
                                  error_messages={"invalid": "Digits only."})


# ------------------------------------------------------------------ pools and pushing leads
class PoolListView(APIView):
    """Counts for every campaign pool. Customers are not listed here, only how many."""

    def get(self, request):
        camps = {c.key: c for c in Campaign.objects.all()}
        out = []
        for key in POOL_KEYS:
            c = camps.get(key)
            if c is None:
                continue
            out.append({"key": key, "label": c.label, "eligible": len(lead_rows(pool_loans(key))), "configured": c.configured,
                        "active": c.active, "last_pushed_at": c.last_pushed_at, "last_pushed_count": c.last_pushed_count})
        return Response({"enabled": enabled(), "in_window": in_window(), "pools": out})


class PoolPushView(APIView):
    """Preview, then (with confirm) push the pool's customers into the Smartflo lead list. Head or Admin."""

    permission_classes = [IsHead]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "bulk"

    def post(self, request, key):
        if key not in POOL_KEYS:
            return bad("Unknown campaign.", status.HTTP_404_NOT_FOUND)
        camp = get_object_or_404(Campaign, key=key)
        rows = lead_rows(pool_loans(key))
        if not request.data.get("confirm"):
            return Response({"preview": True, "eligible": len(rows), "configured": camp.configured, "enabled": enabled(),
                             "sample": [{"account_id": r["field_2"], "name": r["field_1"]} for r in rows[:5]]})
        if not enabled():
            return bad("The dialler is switched off on this server.")
        if not camp.configured:
            return bad("Set the Smartflo campaign id and lead-list id for this campaign first (Admin, Setup).")
        if not rows:
            return bad("Nobody is eligible for this campaign right now.")
        client, sent, error = get_client(), 0, ""
        try:
            for i in range(0, len(rows), CHUNK):
                client.bulk_add_leads(camp.smartflo_list_id, rows[i:i + CHUNK])
                sent += len(rows[i:i + CHUNK])
        except SmartfloError as exc:
            error = str(exc)[:200]
        CampaignPush.objects.create(campaign=camp, by=request.user, leads=sent, ok=not error, error=error)
        if sent:
            camp.last_pushed_at, camp.last_pushed_count = timezone.now(), sent
            camp.save(update_fields=["last_pushed_at", "last_pushed_count"])
        audit(request, "campaign_pushed", user=request.user, object_type="campaign", object_id=camp.pk,
              detail=f"{key}: {sent} of {len(rows)}" + (" (stopped on an error)" if error else ""))
        if error:
            return Response({"detail": f"Smartflo stopped after {sent} of {len(rows)} customers. {error}", "sent": sent}, status=502)
        return Response({"sent": sent})


# ------------------------------------------------------------------ admin setup
class SetupView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        profiles = {p.user_id: p for p in DialerProfile.objects.all()}
        maps = {m.crm_code: m.smartflo_status_id for m in DispositionMap.objects.all()}
        agents = User.objects.filter(role=Role.AGENT, is_active=True).order_by("name")
        return Response({
            "status": {"enabled": bool(settings.SMARTFLO_ENABLED), "token_set": bool(settings.SMARTFLO_API_TOKEN)},
            "campaigns": [{"key": c.key, "label": c.label, "smartflo_campaign_id": c.smartflo_campaign_id,
                           "smartflo_list_id": c.smartflo_list_id, "active": c.active, "configured": c.configured}
                          for c in Campaign.objects.all()],
            "dispositions": [{"code": k, "label": v[0], "smartflo_status_id": maps.get(k, "")} for k, v in DISPOSITIONS.items()],
            "agents": [{"id": a.pk, "name": a.name, "email": a.email,
                        "smartflo_email": profiles[a.pk].smartflo_email if a.pk in profiles else "",
                        "agent_number": profiles[a.pk].agent_number if a.pk in profiles else ""} for a in agents],
        })


class CampaignUpdateSerializer(serializers.Serializer):
    smartflo_campaign_id = _digits_field()
    smartflo_list_id = _digits_field()
    active = serializers.BooleanField(required=False)


class CampaignUpdateView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, key):
        camp = get_object_or_404(Campaign, key=key)
        ser = CampaignUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        for k, v in ser.validated_data.items():
            setattr(camp, k, v)
        if camp.active and not camp.configured:
            return bad("Enter both the Smartflo campaign id and the lead-list id before activating.")
        camp.updated_by = request.user
        camp.save()
        audit(request, "campaign_updated", user=request.user, object_type="campaign", object_id=camp.pk, detail=f"{key} active={camp.active}")
        return Response({"ok": True})


class DispositionMapView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request):
        data = request.data.get("map")
        if not isinstance(data, dict):
            return bad("Send the outcome-to-Smartflo map.")
        for code, sid in data.items():
            if code not in DISPOSITIONS or not str(sid or "").isdigit() and str(sid or "") != "":
                return bad(f"Check the entry for “{DISPOSITION_LABELS.get(code, code)}”: Smartflo ids are numbers.")
        for code, sid in data.items():
            DispositionMap.objects.update_or_create(crm_code=code, defaults={"smartflo_status_id": str(sid or "")})
        audit(request, "dialer_setup_changed", user=request.user, detail="disposition map")
        return Response({"ok": True})


class ProfileSerializer(serializers.Serializer):
    smartflo_email = serializers.EmailField(allow_blank=True, required=False)
    agent_number = _digits_field(20)


class AgentProfileView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, user_id):
        user = get_object_or_404(User, pk=user_id, is_active=True)
        ser = ProfileSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        DialerProfile.objects.update_or_create(user=user, defaults=ser.validated_data)
        audit(request, "dialer_setup_changed", user=request.user, object_type="user", object_id=user.pk, detail="agent mapping")
        return Response({"ok": True})


class CheckView(APIView):
    """Admin: can this server reach Smartflo with the stored token? Returns field names only, never values."""

    permission_classes = [IsAdmin]

    def post(self, request):
        if not enabled():
            return Response({"ok": False, "detail": "Switched off, or no token is set on the server."})
        try:
            data = get_client().probe()
        except SmartfloError as exc:
            return Response({"ok": False, "detail": str(exc)})
        return Response({"ok": True, "fields": sorted(str(k) for k in data.keys())[:20] if isinstance(data, dict) else []})


# ------------------------------------------------------------------ the agent's dialler session
def _profile(user):
    return DialerProfile.objects.filter(user=user).first()


def current_session(user):
    s = DialerSession.objects.filter(user=user, ended_at__isnull=True).select_related("campaign").order_by("-id").first()
    if s and s.token_expires_at <= timezone.now():
        s.ended_at = timezone.now()
        s.save(update_fields=["ended_at"])
        return None, True
    return s, False


class StatusView(APIView):
    """What the agent screen needs before starting: is the dialler on, is this agent mapped, is it calling hours."""

    def get(self, request):
        p = _profile(request.user)
        return Response({"enabled": enabled(), "in_window": in_window(), "window": f"{CALL_START_HOUR:02d}:00-{CALL_END_HOUR:02d}:00",
                         "agent_ready": bool(p and p.smartflo_email), "can_click_to_call": bool(p and p.agent_number)})


class StartSerializer(serializers.Serializer):
    campaign = serializers.ChoiceField(choices=POOL_KEYS)
    smartflo_password = serializers.CharField(max_length=200, trim_whitespace=False)


class SessionStartView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dialer_login"

    def post(self, request):
        ser = StartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if not enabled():
            return bad("The dialler is switched off on this server.")
        if not in_window():
            return bad(f"Calling is only allowed between {CALL_START_HOUR:02d}:00 and {CALL_END_HOUR:02d}:00.")
        p = _profile(request.user)
        if not (p and p.smartflo_email):
            return bad("Your Smartflo login is not set up yet. Ask the Admin to add it under Campaigns, Setup.")
        camp = get_object_or_404(Campaign, key=d["campaign"])
        if not (camp.active and camp.configured):
            return bad("This campaign is not switched on yet.")
        client = get_client()
        old, _expired = current_session(request.user)
        if old:
            try:
                client.logout(unseal(old.token_enc), old.campaign.smartflo_campaign_id)
            except SmartfloError:
                pass
            old.ended_at = timezone.now()
            old.save(update_fields=["ended_at"])
        try:
            token = client.agent_login(p.smartflo_email, d["smartflo_password"])
        except SmartfloError as exc:
            audit(request, "dialer_login_failed", user=request.user, detail=str(exc)[:200])
            if exc.status in (400, 401, 403, 422):
                return bad("Smartflo did not accept that e-mail and password. " + str(exc).split(". ", 1)[-1][:120], 400)
            return bad(str(exc), 502)
        try:
            client.session_create(token, camp.smartflo_campaign_id, camp.label)
            client.session_call(token, camp.smartflo_campaign_id, start=True)
        except SmartfloError as exc:
            return bad(f"Signed in, but Smartflo would not start the session. {exc}", 502)
        s = DialerSession.objects.create(user=request.user, campaign=camp, token_enc=seal(token),
                                         token_expires_at=timezone.now() + timedelta(minutes=TOKEN_MINUTES))
        audit(request, "dialer_started", user=request.user, object_type="campaign", object_id=camp.pk, detail=camp.key)
        return Response({"active": True, "campaign": {"key": camp.key, "label": camp.label}, "expires_at": s.token_expires_at})


class SessionStopView(APIView):
    def post(self, request):
        s, _ = current_session(request.user)
        if s:
            try:
                get_client().logout(unseal(s.token_enc), s.campaign.smartflo_campaign_id)
            except SmartfloError:
                pass  # we still close our side; Smartflo logs the agent out when the token lapses
            s.ended_at = timezone.now()
            s.save(update_fields=["ended_at"])
            audit(request, "dialer_stopped", user=request.user, object_type="campaign", object_id=s.campaign_id)
        return Response({"active": False})


def _grant(user, loans):
    until = timezone.now() + timedelta(hours=ACCESS_HOURS)
    for loan in loans:
        n = CallAccess.objects.filter(user=user, loan=loan).update(expires_at=until)
        if not n:
            CallAccess.objects.create(user=user, loan=loan, expires_at=until)


def _match(number):
    m = "91" + number
    return list(Loan.objects.filter(ACTIVE_Q).filter(Q(borrower_mobile=m) | Q(alt_mobile=m)))


class SessionView(APIView):
    """Polled by the Dialer screen. Shows who the agent is talking to, and keeps the outcome pending until it is released."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def get(self, request):
        s, expired = current_session(request.user)
        if not s:
            return Response({"active": False, "expired": expired})
        p = _profile(request.user)
        out = {"active": True, "campaign": {"key": s.campaign.key, "label": s.campaign.label}, "expires_at": s.token_expires_at,
               "call": None, "candidates": [], "current": None, "awaiting_outcome": False, "warning": "", "unparsed_fields": []}
        calls, unparsed = [], []
        try:
            calls, unparsed = parse_live_calls(get_client().live_calls(unseal(s.token_enc), (p.agent_number if p and p.agent_number else "")))
        except SmartfloError as exc:
            out["warning"] = str(exc)
        out["unparsed_fields"] = unparsed
        if calls:
            call = calls[0]
            matches = _match(call["number"])
            if call["id"] != s.last_unique_id:
                best = max(matches, key=lambda l: l.overdue_amount) if matches else None
                s.last_unique_id, s.last_loan, s.released = call["id"], best, False
                s.save(update_fields=["last_unique_id", "last_loan", "released"])
                _grant(request.user, matches)
            out["call"] = {"state": call["state"]}
            out["candidates"] = [{"id": m.pk, "account_id": m.account_id, "borrower_name": m.borrower_name} for m in matches] if len(matches) > 1 else []
        out["awaiting_outcome"] = bool(s.last_unique_id and not s.released)
        if s.last_loan_id and (out["call"] or out["awaiting_outcome"]):
            loan = scoped(request.user, grants=True).filter(pk=s.last_loan_id).first()
            out["current"] = LoanSerializer(loan).data if loan else None
        return Response(out)


class SessionDismissView(APIView):
    """The call needs no outcome (nobody answered). Clears the pending state without telling Smartflo."""

    def post(self, request):
        s, _ = current_session(request.user)
        if s:
            s.released = True
            s.save(update_fields=["released"])
        return Response({"ok": True})


def _release(s, code):
    """Tell Smartflo the outcome of the last call. Returns (ok, warning)."""
    sid = DispositionMap.objects.filter(crm_code=code).values_list("smartflo_status_id", flat=True).first()
    if not sid:
        return False, f"Saved in the CRM, but no Smartflo disposition is set for “{DISPOSITION_LABELS.get(code, code)}”, so the dialler was not told."
    try:
        get_client().disposition(unseal(s.token_enc), s.last_unique_id, sid)
    except SmartfloError as exc:
        return False, f"Saved in the CRM, but Smartflo did not take the outcome. {exc}"
    s.released = True
    s.save(update_fields=["released"])
    return True, ""


class SessionOutcomeView(APIView):
    """Save the outcome in the CRM (same rules as the normal outcome form), then release the agent in Smartflo."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def post(self, request, pk):
        resp = DispositionView().post(request, pk)
        if resp.status_code != 200:
            return resp
        s, _ = current_session(request.user)
        released, warning = True, ""
        if s and s.last_unique_id and not s.released:
            released, warning = _release(s, request.data.get("code"))
        return Response({"loan": resp.data, "released": released, "warning": warning})


class SessionReleaseView(APIView):
    """Retry telling Smartflo the outcome (after a warning)."""

    def post(self, request):
        s, _ = current_session(request.user)
        code = request.data.get("code")
        if not s or not s.last_unique_id or code not in DISPOSITIONS:
            return bad("Nothing to release.")
        ok, warning = _release(s, code)
        return Response({"released": ok, "warning": warning})


# ------------------------------------------------------------------ click to call
class CallSerializer(serializers.Serializer):
    loan_id = serializers.IntegerField()
    which = serializers.ChoiceField(choices=["borrower", "alt", "ref1", "ref2"])


class CallView(APIView):
    """Place one call from the account screen. The number goes server-to-server to Smartflo and never to the browser."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def post(self, request):
        ser = CallSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if not enabled():
            return bad("The dialler is switched off on this server.")
        if not in_window():
            return bad(f"Calling is only allowed between {CALL_START_HOUR:02d}:00 and {CALL_END_HOUR:02d}:00.")
        p = _profile(request.user)
        if not (p and p.agent_number):
            return bad("Your Smartflo agent number is not set up yet. Ask the Admin to add it under Campaigns, Setup.")
        loan = get_object_or_404(scoped(request.user, grants=True), pk=d["loan_id"])
        if loan.operational_status.strip().lower() not in ("0", "active"):
            return bad("This account is closed.")
        number = normalise_mobile(loan.number_for(d["which"]))
        if not number:
            return bad("There is no valid mobile number for that contact.")
        s, _ = current_session(request.user)
        token = unseal(s.token_enc) if s else settings.SMARTFLO_API_TOKEN
        try:
            get_client().click_to_call(token, p.agent_number, number[2:])
        except SmartfloError as exc:
            return bad(str(exc), 502)
        label = {"borrower": "borrower", "alt": "borrower's second number", "ref1": "reference 1", "ref2": "reference 2"}[d["which"]]
        Activity.objects.create(loan=loan, user=request.user, kind=Activity.Kind.CALL, detail=f"Call placed to {label}")
        audit(request, "call_placed", user=request.user, object_type="loan", object_id=loan.pk, detail=d["which"])
        return Response({"ok": True})
