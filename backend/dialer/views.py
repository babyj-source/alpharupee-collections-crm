import base64
import hashlib
from collections import defaultdict
from datetime import datetime, time, timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
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
from loans.workflow import CALL_END_HOUR, CALL_START_HOUR, CONNECTED, DISPOSITION_LABELS, DISPOSITIONS

from .models import Campaign, CampaignPush, DialerProfile, DialerSession, DispositionMap, PushedLead
from .pools import RULE_DEFAULTS, campaign_loans, clean_rules, lead_pairs, lead_rows, rules_summary
from . import setup as smartflo_setup
from .smartflo import SmartfloClient, SmartfloDisabled, SmartfloError, call_phase, last10, parse_live_calls

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


# ------------------------------------------------------------------ campaigns
def _campaign_row(c, user):
    row = {"key": c.key, "label": c.label, "custom": c.custom, "eligible": len(lead_rows(campaign_loans(c))), "configured": c.configured,
           "active": c.active, "last_pushed_at": c.last_pushed_at, "last_pushed_count": c.last_pushed_count}
    if user.is_head:
        row.update({"smartflo_campaign_id": c.smartflo_campaign_id, "smartflo_list_id": c.smartflo_list_id,
                    "rules": {**RULE_DEFAULTS, **(c.rules or {})} if c.custom else None, "summary": rules_summary(c)})
    else:
        row["mine"] = len(lead_rows(campaign_loans(c).filter(assigned_agent=user)))
    return row


class PoolListView(APIView):
    """Every campaign with how many customers are in it. Customers are not listed here, only how many.
    Agents also get how many of those are allocated to them (that is what gets pushed when they start)."""

    def get(self, request):
        return Response({"enabled": enabled(), "in_window": in_window(),
                         "pools": [_campaign_row(c, request.user) for c in Campaign.objects.all()]})


def push_for_agent(camp, user, client):
    """Send this agent's own customers in the campaign to its Smartflo lead list, skipping anyone already sent today.
    Returns (sent, total, error). Stops at the first Smartflo error; what was sent stays recorded."""
    today = timezone.localdate()
    done = PushedLead.objects.filter(campaign=camp, pushed_on=today).values("loan_id")
    pairs = lead_pairs(campaign_loans(camp).filter(assigned_agent=user).exclude(pk__in=done))
    sent, error = 0, ""
    try:
        for i in range(0, len(pairs), CHUNK):
            chunk = pairs[i:i + CHUNK]
            client.bulk_add_leads(camp.smartflo_list_id, [row for _pk, row in chunk])
            PushedLead.objects.bulk_create([PushedLead(campaign=camp, loan_id=pk, pushed_on=today, user=user) for pk, _row in chunk],
                                           ignore_conflicts=True)
            sent += len(chunk)
    except SmartfloError as exc:
        error = str(exc)[:200]
    if pairs:
        CampaignPush.objects.create(campaign=camp, by=user, leads=sent, ok=not error, error=error)
    if sent:
        camp.last_pushed_at, camp.last_pushed_count = timezone.now(), sent
        camp.save(update_fields=["last_pushed_at", "last_pushed_count"])
    return sent, len(pairs), error


class CampaignWriteSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=60, required=False)
    rules = serializers.DictField(required=False)
    smartflo_campaign_id = _digits_field()
    smartflo_list_id = _digits_field()
    active = serializers.BooleanField(required=False)


def _unique_key(label):
    base = "c_" + (slugify(label).replace("-", "_")[:24] or "campaign")
    key, n = base, 2
    while Campaign.objects.filter(key=key).exists():
        key, n = f"{base}_{n}", n + 1
    return key


class CampaignCreateView(APIView):
    """Head or Admin: add a campaign that picks its customers by rules (days past due, overdue amount, product)."""

    permission_classes = [IsHead]

    def post(self, request):
        ser = CampaignWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        label = (d.get("label") or "").strip()
        if not label:
            return bad("Give the campaign a name.")
        if Campaign.objects.filter(label__iexact=label).exists():
            return bad("A campaign with that name already exists.")
        rules, err = clean_rules(d.get("rules"))
        if err:
            return bad(err)
        camp = Campaign(key=_unique_key(label), label=label, custom=True, rules=rules, sort=100 + Campaign.objects.count(),
                        smartflo_campaign_id=d.get("smartflo_campaign_id", ""), smartflo_list_id=d.get("smartflo_list_id", ""),
                        updated_by=request.user)
        if d.get("active") and not camp.configured:
            return bad("Enter both the Smartflo campaign id and the lead-list id before switching a campaign on.")
        camp.active = bool(d.get("active"))
        camp.save()
        audit(request, "campaign_created", user=request.user, object_type="campaign", object_id=camp.pk, detail=camp.key)
        return Response(_campaign_row(camp, request.user), status=201)


# ------------------------------------------------------------------ admin setup
class SetupView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        profiles = {p.user_id: p for p in DialerProfile.objects.all()}
        maps = {m.crm_code: m.smartflo_status_id for m in DispositionMap.objects.all()}
        agents = User.objects.filter(role=Role.AGENT, is_active=True).order_by("name")
        return Response({
            "status": {"enabled": bool(settings.SMARTFLO_ENABLED), "token_set": bool(settings.SMARTFLO_API_TOKEN),
                       "caller_id_set": bool(settings.SMARTFLO_CALLER_ID), "outcome_list_id": smartflo_setup.outcome_list_id()},
            "dispositions": [{"code": k, "label": v[0], "smartflo_status_id": maps.get(k, "")} for k, v in DISPOSITIONS.items()],
            "agents": [{"id": a.pk, "name": a.name, "email": a.email,
                        "smartflo_email": profiles[a.pk].smartflo_email if a.pk in profiles else "",
                        "agent_number": profiles[a.pk].agent_number if a.pk in profiles else ""} for a in agents],
        })


class SmartfloUsersView(APIView):
    """Admin: the users in the Smartflo account (name, login id, agent number) so CRM agents can be matched to them."""

    permission_classes = [IsAdmin]

    def get(self, request):
        if not enabled():
            return bad("Smartflo is switched off on this server, or no token is set.")
        try:
            return Response({"users": smartflo_setup.users_for_screen(get_client())})
        except SmartfloError as exc:
            return bad(str(exc), 502)


class SmartfloOutcomesView(APIView):
    """Admin: create the call-outcome list in Smartflo and save each outcome's Smartflo id."""

    permission_classes = [IsAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin"

    def post(self, request):
        if not enabled():
            return bad("Smartflo is switched off on this server, or no token is set.")
        try:
            res = smartflo_setup.provision_outcomes(get_client())
        except smartflo_setup.SetupError as exc:
            return bad(str(exc))
        except SmartfloError as exc:
            return bad(f"Smartflo refused the request. {exc}", 502)
        audit(request, "smartflo_outcomes_created", user=request.user, detail=f"list {res['list_id']}, {res['mapped']} mapped")
        return Response(res)


class SmartfloCampaignView(APIView):
    """Admin: create the lead list and the dialler campaign in Smartflo for one CRM campaign and save their ids on it."""

    permission_classes = [IsAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin"

    def post(self, request, key):
        camp = get_object_or_404(Campaign, key=key)
        if not enabled():
            return bad("Smartflo is switched off on this server, or no token is set.")
        try:
            res = smartflo_setup.provision_campaign(camp, get_client())
        except smartflo_setup.SetupError as exc:
            return bad(str(exc))
        except SmartfloError as exc:
            return bad(f"Smartflo refused the request. {exc}", 502)
        audit(request, "smartflo_campaign_created", user=request.user, object_type="campaign", object_id=camp.pk,
              detail=f"{camp.key}: campaign {res['campaign_id']}, list {res['list_id']}")
        return Response({**res, "campaign": _campaign_row(camp, request.user)})


class CampaignUpdateView(APIView):
    """Head or Admin: edit a campaign's Smartflo ids, switch it on or off, and (custom campaigns only) rename it or change its rules."""

    permission_classes = [IsHead]

    def put(self, request, key):
        camp = get_object_or_404(Campaign, key=key)
        ser = CampaignWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if camp.custom:
            if "label" in d:
                label = d["label"].strip()
                if not label or Campaign.objects.filter(label__iexact=label).exclude(pk=camp.pk).exists():
                    return bad("Give the campaign a name that is not already used.")
                camp.label = label
            if "rules" in d:
                rules, err = clean_rules(d["rules"])
                if err:
                    return bad(err)
                camp.rules = rules
        for k in ("smartflo_campaign_id", "smartflo_list_id", "active"):
            if k in d:
                setattr(camp, k, d[k])
        if camp.active and not camp.configured:
            return bad("Enter both the Smartflo campaign id and the lead-list id before switching a campaign on.")
        camp.updated_by = request.user
        camp.save()
        audit(request, "campaign_updated", user=request.user, object_type="campaign", object_id=camp.pk, detail=f"{key} active={camp.active}")
        return Response(_campaign_row(camp, request.user))

    def delete(self, request, key):
        camp = get_object_or_404(Campaign, key=key)
        if not camp.custom:
            return bad("The built-in campaigns cannot be deleted. Switch them off instead.")
        try:
            camp.delete()
        except ProtectedError:
            return bad("Agents have already dialled in this campaign, so it cannot be deleted. Switch it off instead.")
        audit(request, "campaign_deleted", user=request.user, object_type="campaign", detail=key)
        return Response(status=204)


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
    # Despite the name (kept so no migration is needed) this is the agent's Smartflo LOGIN ID: the vendor's own sample signs in with a
    # plain id, not an e-mail address.
    smartflo_email = serializers.RegexField(r"^\S*$", max_length=120, allow_blank=True, required=False,
                                            error_messages={"invalid": "The login id cannot contain spaces."})
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


REFRESH_WITHIN = timedelta(minutes=12)   # renew the agent's token when this little is left (it lasts an hour)


def keep_fresh(s, client):
    """Swap the agent's token for a new one shortly before it lapses, so a shift does not end (and the session call stay running)
    after an hour. If Smartflo will not renew it, nothing changes and the session ends at the old expiry as before."""
    if s.token_expires_at - timezone.now() > REFRESH_WITHIN:
        return
    try:
        new = client.refresh(unseal(s.token_enc))
    except SmartfloError:
        return
    s.token_enc, s.token_expires_at = seal(new), timezone.now() + timedelta(minutes=TOKEN_MINUTES)
    s.save(update_fields=["token_enc", "token_expires_at"])


class StatusView(APIView):
    """What the agent screen needs before starting: is the dialler on, is this agent mapped, is it calling hours."""

    def get(self, request):
        p = _profile(request.user)
        return Response({"enabled": enabled(), "in_window": in_window(), "window": f"{CALL_START_HOUR:02d}:00-{CALL_END_HOUR:02d}:00",
                         "agent_ready": bool(p and p.smartflo_email), "can_click_to_call": bool(p and p.agent_number)})


class StartSerializer(serializers.Serializer):
    campaign = serializers.CharField(max_length=40)
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
            return bad("Your Smartflo login id is not set up yet. Ask the Admin to add it under Campaigns, Setup.")
        camp = Campaign.objects.filter(key=d["campaign"]).first()
        if camp is None:
            return bad("Unknown campaign.")
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
            if "too many login" in str(exc).lower():
                return bad("Smartflo has paused sign-in for your login after several tries. Wait 5 minutes, then try again with the right password.", 429)
            if exc.status in (400, 401, 403, 422):
                return bad("Smartflo did not accept that login id and password. " + str(exc).split(". ", 1)[-1][:120], 400)
            return bad(str(exc), 502)
        try:
            client.session_create(token, camp.smartflo_campaign_id, camp.label)
            try:
                client.session_call(token, camp.smartflo_campaign_id, start=True)
            except SmartfloError as exc:
                # A session from an earlier sign-in (a token that lapsed without a logout) makes Smartflo answer "Agent is on a call."
                # The CRM has no open session for this agent, so end the old one and start again, once.
                if "on a call" not in str(exc).lower():
                    raise
                client.logout(token, camp.smartflo_campaign_id)
                client.session_create(token, camp.smartflo_campaign_id, camp.label)
                client.session_call(token, camp.smartflo_campaign_id, start=True)
        except SmartfloError as exc:
            return bad(f"Signed in, but Smartflo would not start the session. {exc}", 502)
        s = DialerSession.objects.create(user=request.user, campaign=camp, token_enc=seal(token),
                                         token_expires_at=timezone.now() + timedelta(minutes=TOKEN_MINUTES))
        audit(request, "dialer_started", user=request.user, object_type="campaign", object_id=camp.pk, detail=camp.key)
        sent, total, error = push_for_agent(camp, request.user, client)
        if sent or error:
            audit(request, "campaign_pushed", user=request.user, object_type="campaign", object_id=camp.pk,
                  detail=f"{camp.key}: {sent} of {total}" + (" (stopped on an error)" if error else ""))
        return Response({"active": True, "campaign": {"key": camp.key, "label": camp.label}, "expires_at": s.token_expires_at,
                         "push": {"sent": sent, "total": total, "error": error}})


class SessionPushView(APIView):
    """Send the agent's customers again (after a push error, or when new customers were allocated since starting)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def post(self, request):
        s, _ = current_session(request.user)
        if not s:
            return bad("Start the campaign first.")
        if not enabled():
            return bad("The dialler is switched off on this server.")
        sent, total, error = push_for_agent(s.campaign, request.user, get_client())
        if sent or error:
            audit(request, "campaign_pushed", user=request.user, object_type="campaign", object_id=s.campaign_id,
                  detail=f"{s.campaign.key}: {sent} of {total}" + (" (stopped on an error)" if error else ""))
        return Response({"sent": sent, "total": total, "error": error})


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
        if enabled():
            keep_fresh(s, get_client())
        out = {"active": True, "campaign": {"key": s.campaign.key, "label": s.campaign.label}, "expires_at": s.token_expires_at,
               "call": None, "candidates": [], "current": None, "awaiting_outcome": False, "warning": "", "unparsed_fields": []}
        calls, unparsed = [], []
        try:
            calls, unparsed = parse_live_calls(get_client().live_calls(unseal(s.token_enc), (p.agent_number if p and p.agent_number else "")))
        except SmartfloError as exc:
            out["warning"] = str(exc)
        out["unparsed_fields"] = unparsed
        s.last_seen_at, s.on_call = timezone.now(), bool(calls)
        s.save(update_fields=["last_seen_at", "on_call"])
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


# ------------------------------------------------------------------ the head's live view
IDLE_SECONDS = 30  # an agent whose Dialer screen has not checked in for this long is shown as idle


class LiveView(APIView):
    """Head or Admin: how each campaign is going today, who is dialling right now, and the latest outcomes.
    Built from what the CRM already knows (pushes, outcomes, the agents' Dialer screens), so it makes no calls to Smartflo.
    Customers are shown by account id and name only, never phone numbers."""

    permission_classes = [IsHead]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def get(self, request):
        now = timezone.now()
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time.min))
        pushed = defaultdict(set)
        for cid, lid in PushedLead.objects.filter(pushed_on=today).values_list("campaign_id", "loan_id"):
            pushed[cid].add(lid)
        outcomes = defaultdict(set)  # loan -> outcome codes logged today
        all_loans = set().union(*pushed.values()) if pushed else set()
        for lid, code in Activity.objects.filter(kind=Activity.Kind.DISPOSITION, created_at__gte=start, loan_id__in=all_loans).values_list("loan_id", "disposition"):
            outcomes[lid].add(code)
        camps = []
        for c in Campaign.objects.all():
            ids = pushed.get(c.pk, set())
            called = [l for l in ids if l in outcomes]
            camps.append({
                "key": c.key, "label": c.label, "active": c.active, "custom": c.custom,
                "waiting": campaign_loans(c).count(), "pushed_today": len(ids), "called": len(called),
                "connected": sum(1 for l in called if outcomes[l] & CONNECTED),
                "promises": sum(1 for l in called if "ptp_given" in outcomes[l]),
                "paid": sum(1 for l in called if "paid_today" in outcomes[l]),
            })
        agents = []
        sessions = DialerSession.objects.filter(ended_at__isnull=True, token_expires_at__gt=now).select_related("user", "campaign", "last_loan")
        calls_today = {u: n for u, n in Activity.objects.filter(kind=Activity.Kind.DISPOSITION, created_at__gte=start)
                       .values_list("user_id").annotate(n=Count("id"))}
        for s in sessions.order_by("started_at"):
            seen = s.last_seen_at and (now - s.last_seen_at).total_seconds() <= IDLE_SECONDS
            state = "on_call" if (seen and s.on_call) else "waiting" if seen else "idle"
            agents.append({"name": s.user.name, "campaign": s.campaign.label, "started_at": s.started_at, "state": state,
                           "account_id": s.last_loan.account_id if (state == "on_call" and s.last_loan) else "",
                           "borrower_name": s.last_loan.borrower_name if (state == "on_call" and s.last_loan) else "",
                           "outcomes_today": calls_today.get(s.user_id, 0)})
        feed = []
        for a in (Activity.objects.filter(kind=Activity.Kind.DISPOSITION, created_at__gte=start)
                  .select_related("loan", "user").order_by("-created_at", "-id")[:40]):
            feed.append({"at": a.created_at, "agent": a.user.name if a.user else "", "account_id": a.loan.account_id,
                         "borrower_name": a.loan.borrower_name, "outcome": DISPOSITION_LABELS.get(a.disposition, a.disposition),
                         "connected": a.disposition in CONNECTED})
        return Response({"as_of": now, "enabled": enabled(), "campaigns": camps, "agents": agents, "feed": feed})


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
            get_client().click_to_call(token, p.agent_number, number[2:], in_session=bool(s))
        except SmartfloError as exc:
            hint = " Stop the campaign on your Dialer screen to place an ordinary call." if s else ""
            return bad(str(exc) + hint, 502)
        label = {"borrower": "borrower", "alt": "borrower's second number", "ref1": "reference 1", "ref2": "reference 2"}[d["which"]]
        Activity.objects.create(loan=loan, user=request.user, kind=Activity.Kind.CALL, detail=f"Call placed to {label}")
        audit(request, "call_placed", user=request.user, object_type="loan", object_id=loan.pk, detail=d["which"])
        return Response({"ok": True})


def _live_call_for(request, loan):
    """The agent's live call that is to one of this account's numbers, or None. Raises ValueError(message) when we cannot look."""
    if not enabled():
        raise ValueError("The dialler is switched off on this server.")
    p = _profile(request.user)
    if not (p and p.agent_number):
        raise ValueError("Your Smartflo agent number is not set up yet.")
    s, _ = current_session(request.user)
    token = unseal(s.token_enc) if s else settings.SMARTFLO_API_TOKEN
    mine = {last10(loan.number_for(k)) for k in ("borrower", "alt", "ref1", "ref2")} - {""}
    calls, _ = parse_live_calls(get_client().live_calls(token, p.agent_number))
    return token, next((c for c in calls if c["number"] in mine), None)


class CallStatusView(APIView):
    """Is the agent's call on this account ringing, connected, or over? Never returns a phone number or a call id."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def get(self, request):
        try:
            loan_id = int(request.query_params.get("loan"))
        except (TypeError, ValueError):
            return bad("Give the loan id.")
        loan = get_object_or_404(scoped(request.user, grants=True), pk=loan_id)
        try:
            _, call = _live_call_for(request, loan)
        except ValueError as exc:
            return bad(str(exc))
        except SmartfloError as exc:
            return bad(str(exc), 502)
        if call is None:
            return Response({"state": "none", "seconds": 0})
        return Response({"state": call_phase(call["state"]), "seconds": call["seconds"]})


class CallHangupView(APIView):
    """End the agent's live call on this account. The call id comes from Smartflo's own live list, never from the browser."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def post(self, request):
        try:
            loan_id = int(request.data.get("loan_id"))
        except (TypeError, ValueError):
            return bad("Give the loan id.")
        loan = get_object_or_404(scoped(request.user, grants=True), pk=loan_id)
        try:
            token, call = _live_call_for(request, loan)
            if call is None:
                return bad("There is no live call on this account.")
            get_client().hangup(token, call["id"])
        except ValueError as exc:
            return bad(str(exc))
        except SmartfloError as exc:
            return bad(f"{exc} End the call on your phone instead.", 502)
        audit(request, "call_ended", user=request.user, object_type="loan", object_id=loan.pk)
        return Response({"ok": True})
