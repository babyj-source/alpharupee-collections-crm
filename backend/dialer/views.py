import base64
import hashlib
import re
from collections import defaultdict
from datetime import datetime, time, timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.cache import cache
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

from .models import Campaign, CampaignPush, DialAttempt, DialerProfile, DialerSession, DispositionMap, PushedLead
from .pools import RULE_DEFAULTS, campaign_loans, clean_rules, lead_pairs, lead_rows, rules_summary
from . import setup as smartflo_setup
from .smartflo import SmartfloClient, SmartfloDisabled, SmartfloError, call_phase, last10, parse_live_calls, walk_dicts

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
        codes = {m.crm_code: m.smartflo_code for m in DispositionMap.objects.all()}
        agents = User.objects.filter(role=Role.AGENT, is_active=True).order_by("name")
        return Response({
            "status": {"enabled": bool(settings.SMARTFLO_ENABLED), "token_set": bool(settings.SMARTFLO_API_TOKEN),
                       "caller_id_set": bool(settings.SMARTFLO_CALLER_ID), "outcome_list_id": smartflo_setup.outcome_list_id()},
            "dispositions": [{"code": k, "label": v[0], "smartflo_status_id": maps.get(k, ""), "smartflo_code": codes.get(k, "")} for k, v in DISPOSITIONS.items()],
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


class SmartfloRulesView(APIView):
    """Admin: send the CRM's retry rules (dial_status etc.) to a campaign that already exists in Smartflo. key "all" does every connected campaign."""

    permission_classes = [IsAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin"

    def post(self, request, key):
        if not enabled():
            return bad("Smartflo is switched off on this server, or no token is set.")
        camps = Campaign.objects.exclude(smartflo_campaign_id="") if key == "all" else [get_object_or_404(Campaign, key=key)]
        done, failed = [], []
        for camp in camps:
            try:
                smartflo_setup.apply_dial_rules(camp, get_client())
                done.append(camp.label)
            except smartflo_setup.SetupError as exc:
                failed.append(f"{camp.label}: {exc}")
            except SmartfloError as exc:
                failed.append(f"{camp.label}: {str(exc)[:200]}")
        audit(request, "smartflo_rules_updated", user=request.user, detail=f"{len(done)} updated, {len(failed)} failed")
        return Response({"updated": done, "failed": failed})


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
        codes = request.data.get("codes") or {}
        if not isinstance(codes, dict) or any(c not in DISPOSITIONS or not re.fullmatch(r"[A-Za-z0-9]{0,5}", str(v or "")) for c, v in codes.items()):
            return bad("Smartflo codes are letters and digits only, 5 characters at most.")
        for code, sid in data.items():
            defaults = {"smartflo_status_id": str(sid or "")}
            if code in codes:
                defaults["smartflo_code"] = str(codes[code] or "")
            DispositionMap.objects.update_or_create(crm_code=code, defaults=defaults)
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


LEAD_LOOKUPS = 40   # the lead list is shared and paged, so each customer is looked up by phone number; this caps the calls per click


def _lead_ids(client, list_id, rows):
    """Smartflo's own id (lead_id) for each of these leads, found by reading the list."""
    accts = {r["field_2"] for r in rows}
    nums = {last10(r["field_0"]) for r in rows}
    ids = []
    for r in rows[:LEAD_LOOKUPS]:
        for d in walk_dicts(client.list_leads(list_id, number=r["field_0"])):
            if str(d.get("field_2", "")).strip() in accts or last10(str(d.get("field_0", ""))) in nums:
                lid = d.get("lead_id") or d.get("id")
                if lid not in (None, "") and str(lid) not in ids:
                    ids.append(str(lid))
    return ids


def _clear_leads(client, list_id, rows):
    """Remove these leads from the Smartflo list so they are sent as new. Smartflo's guide does not say what the bulk delete's `field` must
    hold (field_0 / field_2 are refused with 422), so, in order: the leads' own ids (bulk delete by lead_id, then one by one), then the list's
    column names. First way Smartflo accepts wins. Returns a warning, "" when cleared; the warning lists what Smartflo said to each try."""
    errors = []

    def attempt(label, fn):
        try:
            fn()
            return True
        except SmartfloError as exc:
            errors.append(f"{label}: {str(exc)[:90]}")
            return False

    try:
        ids = _lead_ids(client, list_id, rows)
    except SmartfloError as exc:
        ids = []
        errors.append(f"read list: {str(exc)[:90]}")
    if ids:
        for field in ("lead_id", "id"):
            if attempt(f"delete by {field}", lambda f=field: client.delete_leads(list_id, f, ids)):
                return ""
        done = 0
        for lid in ids:
            if not attempt("delete one lead", lambda l=lid: client.delete_lead(l)):
                break
            done += 1
        if done == len(ids):
            return ""
    for field, values in (("Account ID", [r["field_2"] for r in rows]), ("Phone Number", [r["field_0"] for r in rows])):
        if attempt(f"delete by {field}", lambda f=field, v=values: client.delete_leads(list_id, f, v)):
            return ""
    return "Smartflo would not clear the old entries. They were sent again anyway. Smartflo said: " + "; ".join(errors)


class SessionResendView(APIView):
    """Start this agent's customers in Smartflo from scratch: remove the ones sent today from the Smartflo list, then send them again.
    Smartflo remembers which leads it has already dialled (and waits before retrying), so a customer tried earlier today would otherwise
    not be rung again."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def post(self, request):
        s, _ = current_session(request.user)
        if not s:
            return bad("Start the campaign first.")
        if not enabled():
            return bad("The dialler is switched off on this server.")
        client, today = get_client(), timezone.localdate()
        mine = PushedLead.objects.filter(campaign=s.campaign, pushed_on=today, user=request.user)
        rows = [row for _pk, row in lead_pairs(Loan.objects.filter(pk__in=mine.values("loan_id")))]
        warning = _clear_leads(client, s.campaign.smartflo_list_id, rows) if rows else ""
        mine.delete()
        sent, total, error = push_for_agent(s.campaign, request.user, client)
        audit(request, "campaign_resent", user=request.user, object_type="campaign", object_id=s.campaign_id,
              detail=f"{s.campaign.key}: {sent} of {total}" + (" (stopped on an error)" if error else ""))
        return Response({"sent": sent, "total": total, "error": error, "warning": warning})


DIAL_STATUS = {1: "New", 2: "Message played", 3: "DTMF pressed", 4: "No answer", 5: "DND", 6: "Busy", 7: "Congestion", 8: "Failed", 9: "Account DND"}
DIALER_STATUS = {0: "New (waiting to be dialled)", -1: "Undisposed", -2: "In account DND list", -3: "In public DND list", -4: "Emergency",
                 -5: "Call dropped", -6: "Channel limit reached", -7: "In account block list"}


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class SessionLeadsView(APIView):
    """What Smartflo itself says about this agent's customers in the campaign's list: dial status, tries. Customers by account id only."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dial"

    def get(self, request):
        s, _ = current_session(request.user)
        if not s:
            return bad("Start the campaign first.")
        if not enabled():
            return bad("The dialler is switched off on this server.")
        queue = _queue(s, request.user)
        client, reply = get_client(), []
        try:
            for l in queue[:LEAD_LOOKUPS]:
                if l.borrower_mobile:
                    reply.append(client.list_leads(s.campaign.smartflo_list_id, number=last10(l.borrower_mobile)))
        except SmartfloError as exc:
            return bad(f"Smartflo would not show the list. {exc}", 502)
        by_acct = {l.account_id: l for l in queue}
        by_num = {last10(l.borrower_mobile): l for l in queue if l.borrower_mobile}
        seen, out = set(), []
        for d in walk_dicts(reply):
            acct, num = str(d.get("field_2", "")).strip(), last10(str(d.get("field_0", "")))
            loan = by_acct.get(acct) or by_num.get(num)
            if not loan or loan.pk in seen:
                continue
            seen.add(loan.pk)
            ds, dd = _num(d.get("dial_status")), _num(d.get("dialer_dial_status"))
            out.append({"account_id": loan.account_id, "borrower_name": loan.borrower_name,
                        "dial_status": DIAL_STATUS.get(ds, str(d.get("dial_status", ""))),
                        "dialer_status": DIALER_STATUS.get(dd, str(d.get("dialer_dial_status", ""))),
                        "call_count": d.get("call_count"), "retry_count": d.get("retry_count"), "picked": d.get("lead_picked")})
        missing = [l.account_id for l in queue if l.pk not in seen]
        return Response({"leads": out, "missing": missing})


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


QUEUE_MAX = 300


def _queue(s, user):
    """This agent's customers sent to Smartflo today for this campaign, in the order they were sent (highest days past due first)."""
    ids = PushedLead.objects.filter(campaign=s.campaign, pushed_on=timezone.localdate(), user=user).values_list("loan_id", flat=True)
    return list(Loan.objects.filter(pk__in=list(ids)).order_by("-dpd", "account_id")[:QUEUE_MAX])


def _live_for_agent(s, user, profile, queue):
    """The agent's live calls. Smartflo's own filter by agent number first; if it shows nothing, every live call on the account, kept
    only where the customer is one of this agent's queue (a dialled call may not carry the agent's number until it is bridged).
    Returns (calls, unparsed, via, warning)."""
    client, warning = get_client(), ""
    try:
        calls, unparsed = parse_live_calls(client.live_calls(unseal(s.token_enc), profile.agent_number if profile and profile.agent_number else None))
        if calls:
            return calls, unparsed, "agent", warning
    except SmartfloError as exc:
        warning, unparsed = str(exc), []
    mine = {last10(x) for loan in queue for x in (loan.borrower_mobile, loan.alt_mobile)} - {""}
    if not mine:
        return [], unparsed, "none", warning
    try:
        calls, more = parse_live_calls(client.live_calls(settings.SMARTFLO_API_TOKEN, None))
    except SmartfloError as exc:
        return [], unparsed, "none", warning or str(exc)
    calls = [c for c in calls if c["number"] in mine]
    return calls, unparsed or more, ("account" if calls else "none"), warning if not calls else ""


def _track_attempts(s, calls, queue, now):
    """Remember each call Smartflo makes for this session, and when it connected and ended. A call that ended without ever connecting
    needs no outcome, so the agent is not held up."""
    by_number = {}
    for loan in queue:
        for x in (loan.borrower_mobile, loan.alt_mobile):
            if last10(x):
                by_number.setdefault(last10(x), loan)
    live_ids = set()
    for c in calls:
        live_ids.add(c["id"])
        phase = call_phase(c["state"])
        loan = by_number.get(c["number"])
        if loan is None:
            matches = _match(c["number"])
            loan = max(matches, key=lambda l: l.overdue_amount) if matches else None
        att, _ = DialAttempt.objects.get_or_create(session=s, call_id=c["id"], defaults={"loan": loan, "started_at": now, "state": phase})
        if att.state == "ended":
            att.state, att.ended_at = phase, None
        if phase == "connected":
            att.state = "connected"
            att.connected_at = att.connected_at or now
        att.last_seen_at = now
        att.save()
    for att in DialAttempt.objects.filter(session=s).exclude(state="ended").exclude(call_id__in=live_ids):
        att.state, att.ended_at = "ended", now
        att.save(update_fields=["state", "ended_at"])
        if att.connected_at is None and s.last_unique_id == att.call_id and not s.released:
            s.released = True
            s.save(update_fields=["released"])


def _queue_rows(s, user, queue, attempts):
    outcomes = {}
    for a in Activity.objects.filter(kind=Activity.Kind.DISPOSITION, user=user, created_at__gte=s.started_at,
                                     loan_id__in=[l.pk for l in queue]).order_by("created_at"):
        outcomes[a.loan_id] = a.disposition
    per_loan = {}
    for att in attempts:
        per_loan.setdefault(att.loan_id, []).append(att)
    rows = []
    for loan in queue:
        mine = per_loan.get(loan.pk, [])
        latest = mine[0] if mine else None            # attempts are newest first
        if latest and latest.state == "connected":
            code, text = "on_call", "On a call"
        elif latest and latest.state == "ringing":
            code, text = "calling", "Calling now"
        elif loan.pk in outcomes:
            code, text = "done", DISPOSITION_LABELS.get(outcomes[loan.pk], "Outcome saved")
        elif latest and any(a.connected_at for a in mine):
            code, text = "outcome_pending", "Spoke · outcome not saved"
        elif latest:
            code, text = "no_answer", "Not answered"
        else:
            code, text = "waiting", "Waiting"
        rows.append({"id": loan.pk, "account_id": loan.account_id, "borrower_name": loan.borrower_name, "dpd": loan.dpd,
                     "overdue_amount": loan.overdue_amount, "status": code, "status_text": text, "attempts": len(mine),
                     "last_at": latest.started_at if latest else None})
    return rows


def _retry_release(s, user):
    """The agent already saved the outcome in the CRM but Smartflo was not told (no Smartflo id for it then). Once an id exists, tell
    Smartflo now, so the agent does not have to save the outcome again. Makes no call to Smartflo while there is still no id."""
    if not (s.last_unique_id and s.last_loan_id and not s.released):
        return
    att = DialAttempt.objects.filter(session=s, call_id=s.last_unique_id).first()
    since = att.started_at if att else s.started_at
    code = (Activity.objects.filter(kind=Activity.Kind.DISPOSITION, user=user, loan_id=s.last_loan_id, created_at__gte=since)
            .order_by("-created_at").values_list("disposition", flat=True).first())
    if not code:
        return
    if not _status_id(code) and cache.add("dialer-outcome-ids-lookup", 1, 300):
        _ensure_outcome_ids()                    # try to find the ids by itself, but not more often than every 5 minutes
    if _status_id(code) and cache.add(f"dialer-release-{s.pk}", 1, 15):
        _release(s, code)


class SessionView(APIView):
    """Polled by the Dialer screen. Shows who the agent is talking to, the agent's queue and what happened to each customer,
    and keeps the outcome pending until it is released."""

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
               "call": None, "candidates": [], "current": None, "awaiting_outcome": False, "warning": "", "unparsed_fields": [],
               "queue": [], "stats": {}, "log": [], "live": {"seen": 0, "via": "none"}}
        now = timezone.now()
        queue = _queue(s, request.user)
        calls, unparsed, via, warning = _live_for_agent(s, request.user, p, queue)
        out["warning"], out["unparsed_fields"], out["live"] = warning, unparsed, {"seen": len(calls), "via": via}
        s.last_seen_at, s.on_call = now, bool(calls)
        s.save(update_fields=["last_seen_at", "on_call"])
        if calls:
            call = calls[0]
            matches = _match(call["number"])
            if call["id"] != s.last_unique_id:
                inq = next((l for l in queue if call["number"] in {last10(l.borrower_mobile), last10(l.alt_mobile)}), None)
                best = inq or (max(matches, key=lambda l: l.overdue_amount) if matches else None)
                s.last_unique_id, s.last_loan, s.released = call["id"], best, False
                s.save(update_fields=["last_unique_id", "last_loan", "released"])
                _grant(request.user, matches)
            out["call"] = {"state": call["state"], "phase": call_phase(call["state"]), "seconds": call["seconds"]}
            out["candidates"] = [{"id": m.pk, "account_id": m.account_id, "borrower_name": m.borrower_name} for m in matches] if len(matches) > 1 else []
        _track_attempts(s, calls, queue, now)
        _retry_release(s, request.user)
        s.refresh_from_db(fields=["released"])
        attempts = list(DialAttempt.objects.filter(session=s).select_related("loan"))
        out["queue"] = _queue_rows(s, request.user, queue, attempts)
        counts = defaultdict(int)
        for r in out["queue"]:
            counts[r["status"]] += 1
        out["stats"] = {"total": len(out["queue"]), "waiting": counts["waiting"], "calling": counts["calling"] + counts["on_call"],
                        "done": counts["done"], "no_answer": counts["no_answer"], "pending": counts["outcome_pending"]}
        for att in attempts[:12]:
            secs = int(((att.ended_at or now) - att.connected_at).total_seconds()) if att.connected_at else 0
            result = ("On a call" if att.state == "connected" else "Ringing" if att.state == "ringing"
                      else f"Spoke {secs // 60}:{secs % 60:02d}" if att.connected_at else "Not answered")
            out["log"].append({"id": att.pk, "at": att.started_at, "account_id": att.loan.account_id if att.loan else "",
                               "borrower_name": att.loan.borrower_name if att.loan else "", "result": result})
        if out["call"] and out["call"]["phase"] == "connected":
            live = next((a for a in attempts if a.state == "connected"), None)
            if live and live.connected_at:
                out["call"]["seconds"] = max(out["call"]["seconds"], int((now - live.connected_at).total_seconds()))
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


def _status_id(code):
    """Smartflo's id for this outcome. If this outcome has none yet, borrow the id of a similar one (same group, then any 'Connected'
    outcome for a connected one, then any) so the agent is still released and the next customer is dialled. The CRM keeps the real outcome."""
    # Smartflo takes only the NUMERIC id for an outcome ("disposition_status must be a number"), never the short code.
    maps = {m.crm_code: str(m.smartflo_status_id).strip() for m in DispositionMap.objects.all() if str(m.smartflo_status_id).strip().isdigit()}
    if maps.get(code):
        return maps[code]
    label, group, _st = DISPOSITIONS.get(code, ("", "", None))
    for pick in (lambda c: DISPOSITIONS[c][1] == group,
                 lambda c: label.startswith("Connected") and DISPOSITIONS[c][0].startswith("Connected"),
                 lambda c: True):
        for c, sid in maps.items():
            if c in DISPOSITIONS and pick(c):
                return sid
    return None


def _ensure_outcome_ids():
    """No outcome has a Smartflo id yet (nobody ran Campaigns, Setup, Call outcomes). Make or find the 'CRM call outcomes' list in Smartflo and
    save every id, so the agent is not stuck. Safe to repeat: it finds what already exists. Returns a short note about what went wrong, if anything."""
    try:
        res = smartflo_setup.provision_outcomes(get_client())
        return " ".join(x for x in res.get("steps", []) if "Could not read" in x or "does not show" in x)[:400]
    except Exception as exc:       # never let set-up trouble hide the real message
        return str(exc)[:200]


def _release(s, code):
    """Tell Smartflo the outcome of the last call. Returns (ok, warning)."""
    sid = _status_id(code)
    note = ""
    if not sid:
        note = _ensure_outcome_ids()
        sid = _status_id(code)
    if not sid:
        return False, (f"Saved in the CRM, but Smartflo has no number for “{DISPOSITION_LABELS.get(code, code)}” yet, so the dialler was not told. "
                       f"An Admin must enter the outcome numbers under Campaigns, Setup, Call outcomes. {note}").strip()
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
    call = next((c for c in calls if c["number"] in mine), None)
    if call is None and s:      # a dialled call may not carry the agent's number yet: look at the account's live calls for this agent's customers
        calls = _live_for_agent(s, request.user, p, _queue(s, request.user))[0]
        call = next((c for c in calls if c["number"] in mine), None)
    return token, call


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
