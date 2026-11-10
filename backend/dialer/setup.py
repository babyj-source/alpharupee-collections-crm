"""Create the Smartflo side of a campaign from the CRM: the lead list, the call-outcome list, and the dialler campaign.

Built from Smartflo's public API reference. That reference documents what to SEND but not what comes back for the create calls
(only success and message), so after every create the CRM reads the lists back and finds the new item by its name. Anything it
cannot find is reported, and the Admin can type the id in by hand as before. Re-running is safe: an item that already exists in
Smartflo under the same name is adopted, not created twice."""
import re

from accounts.models import User
from loans.workflow import DISPOSITIONS

from django.conf import settings

from .models import Campaign, DialerProfile, DispositionMap, SmartfloSetting
from .smartflo import SmartfloError, find_id, walk_dicts

LEAD_FIELDS = ["Phone Number", "Name", "Account ID"]   # field_0, field_1, field_2 as pushed by pools.lead_pairs
OUTCOME_LIST = "CRM call outcomes"
# Smartflo does not take symbols in names, so the outcomes are made there with plain words (letters and spaces) and a short code of
# letters only. The CRM keeps its own labels; the pairing is by the Smartflo id saved against each CRM outcome.
SMARTFLO_OUTCOME_NAMES = {
    "ptp_given": "Connected PTP given",
    "paid_today": "Connected paid today",
    "will_pay_no_date": "Connected will pay no date",
    "refuses": "Connected refuses to pay",
    "dispute": "Connected dispute or complaint",
    "needs_senior": "Connected needs senior agent",
    "reference_contacted": "Reference contacted",
    "wrong_number": "Wrong number",
    "not_reachable": "Not reachable or switched off",
    "no_answer": "No answer",
    "call_back_later": "Call back later",
}


# Smartflo allows at most 5 characters in a code, letters only.
SMARTFLO_OUTCOME_CODES = {
    "ptp_given": "ptp", "paid_today": "paid", "will_pay_no_date": "willp", "refuses": "refus", "dispute": "dispt",
    "needs_senior": "senr", "reference_contacted": "refct", "wrong_number": "wrong", "not_reachable": "unrch",
    "no_answer": "noans", "call_back_later": "cback",
}


def smartflo_outcome(code):
    """(name, code) to create in Smartflo for a CRM outcome: plain words, and a short letters-only code (5 characters at most)."""
    return SMARTFLO_OUTCOME_NAMES[code], SMARTFLO_OUTCOME_CODES[code]

# Settings the CRM chooses for a new campaign. They follow the example Smartflo's dialler team tested ("calls are connecting"):
# ratio dialling 1:1, Dial Out (Session) so each agent has one session call for the shift, agent extension (softphone), callbacks only
# to the same agent. The business rules are ours: up to 3 attempts, 2 hours apart, a 60-second wrap-up after each call, and the
# not-reached and busy customers are dialled again. Review them in Smartflo after the first campaign is made.
CAMPAIGN_DEFAULTS = {
    "dial_method": "2", "dial_ratio": "1",
    "dial_in_type": "3",
    "connect_agent_through": "2",
    "list_traversal_order": "1",                        # oldest lead first; the CRM pushes the highest days-past-due first
    "number_of_retry": "3", "retry_after_minutes": "120",
    "auto_disposition_cancel_duration": "60", "ring_timeout": "30", "queue_timeout": "10", "ring_strategy": "1",
    "dial_status": ["0", "-1", "-5", "-8", "-9"],       # Smartflo's codes: new, undisposed (dialled, no outcome), call drop, missed by agent, queue missed
    "hide_lead_details": True, "update_lead_details": True, "agent_only_callback": True, "enable_web_form": 0,
}


def clean_name(text, prefix="", limit=60):
    """A name Smartflo accepts: letters, digits and single spaces only (no dashes, dots, slashes or other symbols)."""
    t = re.sub(r"[^A-Za-z0-9]+", " ", f"{prefix} {text}").strip()
    return t[:limit].strip()


RULE_KEYS = ("number_of_retry", "retry_after_minutes", "dial_status")


def apply_dial_rules(camp, client):
    """Push the CRM's retry rules (how many times, how long between, which lead statuses are dialled) to a campaign already made in
    Smartflo. Campaigns made before the dial_status codes were corrected only dialled brand-new leads, so a customer Smartflo had
    already tried once (status Undisposed) was never rung again."""
    if not camp.smartflo_campaign_id:
        raise SetupError("This campaign is not connected to Smartflo yet.")
    body = {k: CAMPAIGN_DEFAULTS[k] for k in RULE_KEYS}
    client.update_campaign(camp.smartflo_campaign_id, body)
    return body


class SetupError(Exception):
    """A problem the Admin can fix; the message says how."""


def _get(key):
    row = SmartfloSetting.objects.filter(key=key).first()
    return row.value if row else ""


def _put(key, value):
    SmartfloSetting.objects.update_or_create(key=key, defaults={"value": str(value)})


def outcome_list_id():
    return _get("disposition_list_id")


def _smartflo_users(client):
    return [d for d in walk_dicts(client.list_users()) if "login_id" in d]


def users_for_screen(client):
    """Smartflo users as {user_id, name, login_id, agent_number}, so the Admin can map CRM agents."""
    out = []
    for d in _smartflo_users(client):
        agent = d.get("agent") if isinstance(d.get("agent"), dict) else {}
        out.append({"user_id": d.get("id"), "name": str(d.get("name") or ""), "login_id": str(d.get("login_id") or ""),
                    "agent_number": str(agent.get("id") or "")})
    return out


def provision_outcomes(client):
    """Make the 'CRM call outcomes' list in Smartflo, add the 11 outcomes, and save each outcome's Smartflo id."""
    steps = []
    listing = client.list_disposition_lists()
    list_id = find_id(listing, name=OUTCOME_LIST)
    if list_id:
        steps.append("The outcomes list already exists in Smartflo; using it.")
    else:
        client.create_disposition_list(OUTCOME_LIST, "Call outcomes used by the Collections CRM")
        list_id = find_id(client.list_disposition_lists(), name=OUTCOME_LIST)
        if not list_id:
            raise SetupError("Smartflo created the outcomes list but did not say which id it has. Open Smartflo, copy the list id, "
                             "and ask IT to store it, or enter each outcome id by hand in the table.")
        steps.append("Created the outcomes list in Smartflo.")
    _put("disposition_list_id", list_id)

    existing = client.list_disposition_lists()
    added, missing = 0, []
    for code, (label, _g, _s) in DISPOSITIONS.items():
        name, plain = smartflo_outcome(code)
        DispositionMap.objects.update_or_create(crm_code=code, defaults={"smartflo_code": plain})   # the codes are kept in the CRM too
        if find_id(existing, name=name, code=plain) or find_id(existing, name=label, code=code):
            continue
        try:
            client.add_disposition_status(list_id, name, plain)
            added += 1
        except SmartfloError as exc:
            if "already" not in str(exc).lower():      # "name/code already taken": it was made by hand in Smartflo, which is fine
                raise
    after = client.list_disposition_lists() if added else existing
    # The list reply does not show each outcome's id, but Smartflo's guide has a call that does: read the outcomes of our list.
    found_via = ""
    try:
        reader = getattr(client, "list_disposition_statuses", None)
        if reader:
            statuses = reader(list_id)
            after = {"lists": after, "statuses": statuses}
            found_via = "Read the outcomes' ids from Smartflo's outcome list."
    except SmartfloError as exc:
        steps.append(f"Could not read the outcome ids from Smartflo ({str(exc)[:160]}).")
    except Exception:
        pass
    if found_via:
        steps.append(found_via)
    mapped = 0
    for code, (label, _g, _s) in DISPOSITIONS.items():
        name, plain = smartflo_outcome(code)
        sid = None
        for d in walk_dicts(after):    # an item that is one of OUR outcomes (by code or name), not the list itself
            if d.get("id") in (None, "") or str(d["id"]) == str(list_id):
                continue
            c, n = str(d.get("code", "")).lower(), str(d.get("name", "")).strip().lower()
            if c in (code, plain) or n in (name.lower(), label.lower()):
                sid = str(d["id"])
                break
        if sid and sid.isdigit():
            DispositionMap.objects.update_or_create(crm_code=code, defaults={"smartflo_status_id": sid})
            mapped += 1
        else:
            missing.append(name)
    steps.append(f"Added {added} outcome(s); saved Smartflo ids for {mapped} of {len(DISPOSITIONS)}.")
    if missing:
        keys = sorted({k for d in list(walk_dicts(after))[:6] for k in d})
        steps.append("Smartflo does not show the outcomes' ids in its list reply (it shows: " + ", ".join(keys) + "). Smartflo needs each outcome's NUMBER "
                     "when the CRM reports a call's outcome, so type the numbers in the table (Smartflo: Select an Action, open the outcome and "
                     "read the number in its address), or ask Smartflo for them.")
    return {"list_id": list_id, "steps": steps, "mapped": mapped, "missing": missing}


def caller_did_id(client):
    """The id Smartflo uses for the outgoing number (campaigns take the id, not the number). SMARTFLO_CALLER_DID_ID wins if set;
    otherwise the account's number list is searched for SMARTFLO_CALLER_ID."""
    if settings.SMARTFLO_CALLER_DID_ID:
        return str(settings.SMARTFLO_CALLER_DID_ID)
    if not settings.SMARTFLO_CALLER_ID:
        raise SetupError("The outgoing number is not set on the server (SMARTFLO_CALLER_ID). Ask IT to run set-smartflo.sh caller <number>.")
    want = "".join(c for c in settings.SMARTFLO_CALLER_ID if c.isdigit())
    items = [d for d in walk_dicts(client.list_my_numbers()) if d.get("id") not in (None, "") and ("did" in d or "alias" in d)]
    for d in items:
        for field in ("did", "alias", "name"):
            have = "".join(c for c in str(d.get(field) or "") if c.isdigit())
            if have and (have == want or have.endswith(want) or want.endswith(have) and len(have) >= 8):
                return str(d["id"])
    shown = ", ".join(f"{d.get('did') or d.get('alias') or d.get('name')} (id {d['id']})" for d in items[:8]) or "none returned"
    raise SetupError(f"The outgoing number {settings.SMARTFLO_CALLER_ID} was not found in Smartflo's number list. Numbers on the account: {shown}. "
                     "If one of these is the right one, ask IT to run set-smartflo.sh did <id>.")


def campaign_body(camp, list_id, disposition_list_id, agent_ids, did_id):
    body = dict(CAMPAIGN_DEFAULTS)
    body.update({
        "name": clean_name(camp.label, "CRM"), "description": clean_name(camp.label, "Collections CRM campaign", 200),
        "outbound_caller_id": [str(did_id)],
        "lead_list_map": [{"list_id": str(list_id), "list_status": "1"}],
        "disposition_list": str(disposition_list_id),
        "agent": agent_ids,
    })
    return body


def provision_campaign(camp, client):
    """Make the lead list and the dialler campaign for one CRM campaign and save their ids on it."""
    if camp.configured:
        raise SetupError("This campaign already has its Smartflo ids.")
    steps = []
    dlist = outcome_list_id()
    if not dlist:
        # Smartflo wants an outcome list on every campaign and an outcome for every answered call. The agent still saves the outcome
        # once, on the CRM customer page; the CRM passes it on. So the list is made here, without a separate step for the Admin.
        out = provision_outcomes(client)
        dlist = out["list_id"]
        steps.extend(out["steps"])
    profiles = {p.smartflo_email.strip().lower(): p for p in DialerProfile.objects.exclude(smartflo_email="") if User.objects.filter(pk=p.user_id, is_active=True).exists()}
    agent_ids = sorted({int(d["id"]) for d in _smartflo_users(client) if str(d.get("login_id", "")).strip().lower() in profiles
                        and str(d.get("id", "")).isdigit()})
    if not agent_ids:
        raise SetupError("No CRM agent could be matched to a Smartflo user. Fill in each agent's Smartflo login id under Setup first.")

    list_name = clean_name(camp.label, "CRM")
    list_id = camp.smartflo_list_id or find_id(client.list_lead_lists(), name=list_name)
    if list_id:
        steps.append("The lead list already exists in Smartflo; using it.")
    else:
        try:
            client.create_lead_list(list_name, clean_name(camp.label, "Customers for", 200), LEAD_FIELDS)
        except SmartfloError as exc:
            raise SetupError(f"Smartflo would not create the lead list “{list_name}”. {exc}") from exc
        list_id = find_id(client.list_lead_lists(), name=list_name)
        if not list_id:
            raise SetupError("Smartflo created the lead list but did not say which id it has. Copy it from Smartflo and enter it by hand.")
        steps.append("Created the lead list in Smartflo (columns: Phone Number, Name, Account ID).")
    camp.smartflo_list_id = list_id
    camp.save(update_fields=["smartflo_list_id"])

    name = clean_name(camp.label, "CRM")
    cid = find_id(client.list_campaigns(), name=name)
    if cid:
        steps.append("A Smartflo campaign with this name already exists; using it.")
    else:
        body = campaign_body(camp, list_id, dlist, agent_ids, caller_did_id(client))
        try:
            client.create_campaign(body)
        except SmartfloError as exc:
            raise SetupError(f"Smartflo would not create the campaign “{name}”. {exc} (The lead list was made and is kept; "
                             f"press Create in Smartflo again after fixing this.)") from exc
        cid = find_id(client.list_campaigns(), name=name)
        if not cid:
            raise SetupError("Smartflo created the campaign but did not say which id it has. The lead list id was saved; "
                             "copy the campaign id from Smartflo and enter it by hand.")
        steps.append(f"Created the dialler campaign in Smartflo with {len(agent_ids)} agent(s).")
    camp.smartflo_campaign_id = cid
    camp.save(update_fields=["smartflo_campaign_id"])
    return {"campaign_id": cid, "list_id": list_id, "agents": len(agent_ids), "steps": steps}
