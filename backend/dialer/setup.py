"""Create the Smartflo side of a campaign from the CRM: the lead list, the call-outcome list, and the dialler campaign.

Built from Smartflo's public API reference. That reference documents what to SEND but not what comes back for the create calls
(only success and message), so after every create the CRM reads the lists back and finds the new item by its name. Anything it
cannot find is reported, and the Admin can type the id in by hand as before. Re-running is safe: an item that already exists in
Smartflo under the same name is adopted, not created twice."""
from accounts.models import User
from loans.workflow import DISPOSITIONS

from django.conf import settings

from .models import Campaign, DialerProfile, DispositionMap, SmartfloSetting
from .smartflo import SmartfloError, find_id, walk_dicts

LEAD_FIELDS = ["Phone Number", "Name", "Account ID"]   # field_0, field_1, field_2 as pushed by pools.lead_pairs
OUTCOME_LIST = "CRM call outcomes"

# Settings the CRM chooses for a new campaign. They follow the agreed defaults (3 attempts, 2 hours apart, 30 second ring) and
# one call per agent at a time. Nothing here is secret; review them in the Smartflo portal after the first campaign is made.
CAMPAIGN_DEFAULTS = {
    "dial_method": "2", "dial_ratio": "1",              # Ratio 1:1 = one customer per free agent (progressive)
    "dial_in_type": "3",                                # Dial Out (Session): the agent's session call, as the CRM starts it
    "connect_agent_through": "1",                       # agent mobile
    "list_traversal_order": "1",                        # oldest lead first
    "number_of_retry": "3", "retry_after_minutes": "120",
    "auto_disposition_cancel_duration": "60", "ring_timeout": "30",
    "dial_status": ["0", "3", "9"],                     # new, not reachable, busy
    "hide_lead_details": 0, "update_lead_details": 0, "enable_web_form": 0, "agent_only_callback": 0,
}


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
        if find_id(existing, name=label, code=code):
            continue
        client.add_disposition_status(list_id, label, code)
        added += 1
    after = client.list_disposition_lists() if added else existing
    mapped = 0
    for code, (label, _g, _s) in DISPOSITIONS.items():
        sid = None
        for d in walk_dicts(after):    # an item that is one of OUR outcomes (by code or name), not the list itself
            if d.get("id") in (None, "") or str(d["id"]) == str(list_id):
                continue
            if str(d.get("code", "")).lower() == code or str(d.get("name", "")).strip().lower() == label.lower():
                sid = str(d["id"])
                break
        if sid and sid.isdigit():
            DispositionMap.objects.update_or_create(crm_code=code, defaults={"smartflo_status_id": sid})
            mapped += 1
        else:
            missing.append(label)
    steps.append(f"Added {added} outcome(s); saved Smartflo ids for {mapped} of {len(DISPOSITIONS)}.")
    if missing:
        steps.append("Smartflo did not show an id for: " + ", ".join(missing) + ". Enter those ids in the table below.")
    return {"list_id": list_id, "steps": steps, "mapped": mapped, "missing": missing}


def campaign_body(camp, list_id, disposition_list_id, agent_ids):
    if not settings.SMARTFLO_CALLER_ID:
        raise SetupError("The outgoing number is not set on the server (SMARTFLO_CALLER_ID). Ask IT to run set-smartflo.sh caller <number>.")
    body = dict(CAMPAIGN_DEFAULTS)
    body.update({
        "name": f"CRM {camp.label}"[:60], "description": f"Collections CRM campaign: {camp.label}",
        "outbound_caller_id": [settings.SMARTFLO_CALLER_ID],
        "lead_list_map": [{"list_id": int(list_id), "list_status": 1}],
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
        raise SetupError("Create the call outcomes in Smartflo first (Setup, Call outcomes in Smartflo).")
    profiles = {p.smartflo_email.strip().lower(): p for p in DialerProfile.objects.exclude(smartflo_email="") if User.objects.filter(pk=p.user_id, is_active=True).exists()}
    agent_ids = sorted({int(d["id"]) for d in _smartflo_users(client) if str(d.get("login_id", "")).strip().lower() in profiles
                        and str(d.get("id", "")).isdigit()})
    if not agent_ids:
        raise SetupError("No CRM agent could be matched to a Smartflo user. Fill in each agent's Smartflo login id under Setup first.")

    list_name = f"CRM {camp.label}"[:60]
    list_id = camp.smartflo_list_id or find_id(client.list_lead_lists(), name=list_name)
    if list_id:
        steps.append("The lead list already exists in Smartflo; using it.")
    else:
        client.create_lead_list(list_name, f"Customers for {camp.label}", LEAD_FIELDS)
        list_id = find_id(client.list_lead_lists(), name=list_name)
        if not list_id:
            raise SetupError("Smartflo created the lead list but did not say which id it has. Copy it from Smartflo and enter it by hand.")
        steps.append("Created the lead list in Smartflo (columns: Phone Number, Name, Account ID).")
    camp.smartflo_list_id = list_id
    camp.save(update_fields=["smartflo_list_id"])

    name = f"CRM {camp.label}"[:60]
    cid = find_id(client.list_campaigns(), name=name)
    if cid:
        steps.append("A Smartflo campaign with this name already exists; using it.")
    else:
        client.create_campaign(campaign_body(camp, list_id, dlist, agent_ids))
        cid = find_id(client.list_campaigns(), name=name)
        if not cid:
            raise SetupError("Smartflo created the campaign but did not say which id it has. The lead list id was saved; "
                             "copy the campaign id from Smartflo and enter it by hand.")
        steps.append(f"Created the dialler campaign in Smartflo with {len(agent_ids)} agent(s).")
    camp.smartflo_campaign_id = cid
    camp.save(update_fields=["smartflo_campaign_id"])
    return {"campaign_id": cid, "list_id": list_id, "agents": len(agent_ids), "steps": steps}
