"""Smartflo (Tata Tele) client. Built from the vendor's Postman collection and e-mail; response shapes are read defensively
because they have not been checked against the live service yet (see docs/PHASE1_INCREMENT4.md).

Never logs or returns tokens, passwords, request bodies or phone numbers."""
import logging
import re

import requests
from django.conf import settings


log = logging.getLogger(__name__)


class SmartfloError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class SmartfloDisabled(SmartfloError):
    pass


def _flatten(v, depth=0):
    """Short text from a validation-error body: {"errors": {"agent": ["The agent field is required."]}} -> agent: The agent field is required."""
    if depth > 3:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_flatten(x, depth + 1)}" if not str(k).isdigit() else _flatten(x, depth + 1) for k, x in list(v.items())[:6])
    if isinstance(v, list):
        return ", ".join(_flatten(x, depth + 1) for x in v[:6])
    return ""


def _msg(resp):
    """What Smartflo said, for the screen: its message, plus the field errors of a 422. Never the request."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            text = str(data.get("message") or data.get("error") or data.get("detail") or "")
            more = _flatten(data.get("errors") or data.get("error_details") or "")
            if more and more not in text:
                text = f"{text} {more}".strip()
        else:
            text = ""
    except ValueError:
        text = ""
    if not text:      # nothing readable in it: show the start of the raw reply (an error page, plain text) so it can be diagnosed
        raw = re.sub(r"<[^>]+>", " ", str(getattr(resp, "text", "") or ""))
        text = f"Reply: {raw}" if raw.strip() else ""
    return re.sub(r"\s+", " ", str(text)).strip()[:300]


def _id(v):
    return int(v) if str(v).isdigit() else v


class SmartfloClient:
    def __init__(self, http=None):
        self.http = http or requests

    _prefer = {}  # remembered header form per endpoint, so a fallback is only needed once

    def _send(self, method, path, token, bearer, json, params, base=None):
        headers = {"accept": "application/json", "content-type": "application/json",
                   "Authorization": f"Bearer {token}" if bearer else token}
        try:
            r = self.http.request(method, (base or settings.SMARTFLO_API_BASE).rstrip("/") + path, headers=headers, json=json,
                                  params=params, timeout=settings.SMARTFLO_TIMEOUT_SECONDS)
        except requests.RequestException:
            raise SmartfloError("Could not reach Smartflo.")
        if r.status_code >= 400:
            raise SmartfloError(f"Smartflo refused the request ({r.status_code}). {_msg(r)}".strip(), status=r.status_code)
        try:
            return r.json()
        except ValueError:
            return {}

    def _call(self, method, path, token, *, bearer=False, json=None, params=None, base=None):
        """The vendor's examples send the token plain on some endpoints and as 'Bearer <token>' on others, and the document
        does not cover every endpoint. If Smartflo rejects the TOKEN (not a wrong password), try the other form once and
        remember which one worked."""
        if not settings.SMARTFLO_ENABLED:
            raise SmartfloDisabled("The dialler is switched off on this server.")
        if not token:
            raise SmartfloError("No Smartflo token is set.")
        key = (base or "", method, re.sub(r"\d+", "#", path))
        first = self._prefer.get(key, bearer)
        try:
            data = self._send(method, path, token, first, json, params, base)
        except SmartfloError as exc:
            msg = str(exc).lower()
            # "Unauthenticated user" is the same kind of refusal as a bad token (wrong header form). Never for sign-in: a wrong
            # password must not be tried twice, because Smartflo locks a login after too many attempts.
            if exc.status != 401 or path == "/v1/auth/login" or not ("token" in msg or "unauthenticated" in msg):
                raise
            try:
                data = self._send(method, path, token, not first, json, params, base)
            except SmartfloError:
                raise SmartfloError(str(exc) + " Tried the token with and without “Bearer”; check it is the right token and still valid.",
                                    status=401)
            self._prefer[key] = not first
            return data
        self._prefer[key] = first
        return data

    # ---- account-level token (set on the server)
    def agent_login(self, email, password):
        data = self._call("POST", "/v1/auth/login", settings.SMARTFLO_API_TOKEN, json={"email": email, "password": password})
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        token = data.get("access_token") or data.get("token") or inner.get("access_token") or inner.get("token")
        if not token:
            raise SmartfloError("Smartflo sign-in did not return a token.")
        return token

    def bulk_add_leads(self, list_id, rows):
        return self._call("POST", f"/v1/broadcast/lead/{list_id}", settings.SMARTFLO_API_TOKEN,
                          json={"data": rows, "duplicate_option": "overwrite"})

    # ---- set-up calls (account token), from Smartflo's public API reference (docs.smartflo.tatatelebusiness.com)
    def _all_pages(self, path, limit=100):
        """Every page of a list call. Smartflo lists come a page at a time (has_more / last_seen_id); reading only the first page
        made the CRM miss things that already exist (and try to create them again). Returns {"data": [reply, reply, ...]}."""
        replies, last = [], None
        for _ in range(30):
            params = {"limit": limit}
            if last is not None:
                params["last_seen_id"] = last
            try:
                data = self._call("GET", path, settings.SMARTFLO_API_TOKEN, bearer=True, params=params)
            except SmartfloError as exc:
                if last is None and exc.status in (400, 422):      # this list does not take paging inputs: ask plainly
                    data = self._call("GET", path, settings.SMARTFLO_API_TOKEN, bearer=True)
                else:
                    raise
            replies.append(data)
            d = data if isinstance(data, dict) else {}
            nxt = d.get("last_seen_id")
            if not (d.get("has_more") or d.get("hasMore")) or nxt in (None, "") or nxt == last:
                break
            last = nxt
        return {"data": replies}

    def list_users(self, limit=200):
        return self._all_pages("/v1/users", limit)

    def list_lead_lists(self):
        return self._all_pages("/v1/broadcast/lists")

    def create_lead_list(self, name, description, fields):
        return self._call("POST", "/v1/broadcast/list", settings.SMARTFLO_API_TOKEN, bearer=True,
                          json={"name": name, "description": description, "field": fields, "enable_outbound_based_skill": "0"})

    def list_campaigns(self):
        return self._all_pages("/v1/dialer/campaign")

    def create_campaign(self, body):
        """Smartflo's tested route for a dialler campaign (their e-mail): cloudphone host, account token sent plain. It answers
        only success and a message; the CRM reads the campaign list back to find the new id."""
        data = self._call("POST", "/v1/outbound-dialer/add-dialer-campaign", settings.SMARTFLO_API_TOKEN, json=body, base=settings.SMARTFLO_CLOUDPHONE_BASE)
        _refusal(data, "Smartflo did not create the campaign.")
        return data

    def list_my_numbers(self):
        """Outgoing numbers on the account with their ids; a campaign's outbound_caller_id is the id."""
        return self._call("GET", "/v1/my_number", settings.SMARTFLO_API_TOKEN)

    def list_disposition_lists(self):
        return self._all_pages("/v1/dialer/disposition_list")

    def create_disposition_list(self, name, description):
        return self._call("POST", "/v1/dialer/disposition_list", settings.SMARTFLO_API_TOKEN, bearer=True,
                          json={"name": name, "description": description, "status": True})

    def list_disposition_statuses(self, list_id):
        """The outcomes inside one list, WITH their ids (Smartflo's dialler guide: get-disposition-status/<list id>). The list call
        above does not show them. Same host and token as campaign create; the token is tried both ways."""
        return self._call("GET", f"/v1/outbound-dialer/get-disposition-status/{list_id}", settings.SMARTFLO_API_TOKEN, bearer=True,
                          base=settings.SMARTFLO_CLOUDPHONE_BASE)

    def add_disposition_status(self, list_id, name, code):
        return self._call("POST", "/v1/dialer/disposition_status", settings.SMARTFLO_API_TOKEN, bearer=True,
                          json={"name": name, "code": code, "dispositionList": str(list_id)})

    def call_records(self, from_date, to_date, page=1, limit=50, call_id=None, call_type=None, direction=None):
        """Call detail records (GET /v1/call/records): dates as 'Y-m-d H:i:s'. Each record may carry a recording_url."""
        params = {"from_date": from_date, "to_date": to_date, "page": str(page), "limit": str(limit)}
        for k, v in (("call_id", call_id), ("call_type", call_type), ("direction", direction)):
            if v:
                params[k] = v
        return self._call("GET", "/v1/call/records", settings.SMARTFLO_API_TOKEN, bearer=True, params=params)

    def list_leads(self, list_id, number=None):
        """The leads in a list with Smartflo's own status for each (dial_status, dialer_dial_status, call_count, retry_count, lead_picked).
        `number` asks for one phone number only: the list is shared by every agent and comes back a page at a time."""
        return self._call("GET", f"/v1/broadcast/leads/{list_id}", settings.SMARTFLO_API_TOKEN, bearer=True,
                          params={"number": str(number)} if number else None)

    def update_campaign(self, campaign_id, body):
        """Change settings of a dialler campaign (every field optional; only the ones sent change)."""
        return self._call("PUT", f"/v1/dialer/campaign/{campaign_id}", settings.SMARTFLO_API_TOKEN, bearer=True, json=body)

    def delete_lead(self, lead_id):
        """Remove one lead by its own id (the lead_id Smartflo shows when the list is read)."""
        return self._call("DELETE", f"/v1/broadcast/lead/{lead_id}", settings.SMARTFLO_API_TOKEN, bearer=True)

    def delete_leads(self, list_id, field, values):
        """Remove leads from a lead list by the value of one column (for example field_0 = mobile)."""
        return self._call("DELETE", f"/v1/broadcast/leads/{list_id}", settings.SMARTFLO_API_TOKEN, bearer=True,
                          json={"field": field, "data": [str(v) for v in values]})

    def probe(self):
        return self._call("GET", "/v1/users", settings.SMARTFLO_API_TOKEN, bearer=True, params={"limit": 1})  # the vendor's example uses Bearer here

    # ---- the agent's own token
    def session_create(self, token, campaign_id, name):
        """Smartflo answers 422 "You are already logged in to this campaign. Initiate session call to start dialing." when the agent
        still has a session from before. That is fine: the next step (the session call) is what starts the dialling."""
        try:
            return self._call("POST", "/v1/dialer/session/create", token, bearer=True,
                              json={"campaign_id": _id(campaign_id), "campaign_name": name})
        except SmartfloError as exc:
            if "already logged in" in str(exc).lower():
                return {"success": True, "already_logged_in": True}
            raise

    def session_call(self, token, campaign_id, start=True):
        """One session call per agent, for the whole shift (not one per customer). Smartflo answers "Agent is on a call." if one is running."""
        data = self._call("POST", "/v1/dialer/session_call", token, bearer=True,
                          json={"startOrEnd": bool(start), "campaignId": str(campaign_id), "logout": False})
        _refusal(data, "Smartflo did not start the session call.")
        return data

    def logout(self, token, campaign_id):
        """Log the agent out of the campaign. "Agent is not logged in." (already out) is not an error."""
        try:
            data = self._call("POST", "/v1/dialer/logout", token, bearer=True, json={"campaign_id": str(campaign_id)})
        except SmartfloError as exc:
            if "not logged in" in str(exc).lower():
                return {}
            raise
        d = data if isinstance(data, dict) else {}
        if d.get("success") is False and "not logged in" not in str(d.get("message", "")).lower():
            raise SmartfloError("Smartflo did not log the agent out. " + str(d.get("message") or "")[:120])
        return data

    def refresh(self, token):
        """A fresh 60-minute token for the same agent, so a long shift does not need the password again. Returns the new token."""
        data = self._call("POST", "/v1/auth/refresh", token, bearer=True)
        new = data.get("access_token") if isinstance(data, dict) else None
        if not new:
            raise SmartfloError("Smartflo did not give a new token.")
        return new

    def disposition(self, token, unique_id, status_id):
        """Tell the dialler the outcome of the call just ended, which frees the agent for the next customer."""
        data = self._call("POST", settings.SMARTFLO_DISPOSITION_PATH, token, bearer=True,   # an agent's token, like session/create
                          json={"disposition_status": str(status_id), "unique_id": str(unique_id)})
        _refusal(data, "Smartflo did not take the outcome.")
        return data

    def live_calls(self, token, agent_number=None):
        """Calls in progress. With an agent number: that agent's. Without: every live call on the account (account token)."""
        return self._call("GET", "/v1/live_calls", token, params={"agent_number": agent_number} if agent_number else None)

    def click_to_call(self, token, agent_number, destination, in_session=False):
        """in_session: the agent has a running dialler session, so this is Smartflo's 'manual dial from the dialler panel'
        (type "dialer", no caller id: the campaign decides). Outside a session type "dialer" is refused, so it is left out."""
        body = {"agent_number": agent_number, "destination_number": destination, "async": 1}
        if in_session:
            body["type"] = "dialer"
        elif settings.SMARTFLO_CALLER_ID:
            body["caller_id"] = settings.SMARTFLO_CALLER_ID          # the account's outgoing number, as in Smartflo's own example
            body["call_timeout"] = settings.SMARTFLO_CALL_TIMEOUT
        if settings.SMARTFLO_CLICK_TO_CALL_TYPE and not in_session:
            body["type"] = settings.SMARTFLO_CLICK_TO_CALL_TYPE
        data = self._call("POST", "/v1/click_to_call", token, json=body)
        # Smartflo can answer HTTP 200 and still say it did not place the call. Log only the verdict fields (never numbers).
        d = data if isinstance(data, dict) else {}
        verdict = {k: d[k] for k in ("success", "status", "message", "error", "code") if k in d and not isinstance(d[k], (dict, list))}
        log.info("Smartflo click_to_call replied: %s", verdict or "(no verdict fields)")
        text = str(d.get("message") or d.get("error") or "")
        # An explicit failure flag, or (when Smartflo gives no success flag at all) a message that reads as a refusal,
        # for example "No agent session details were found, please check."
        refusal = "success" not in d and re.search(r"\b(no|not|fail\w*|invalid|error|unable|cannot|denied)\b", text, re.I)
        failed = d.get("success") is False or str(d.get("status", "")).strip().lower() in ("false", "failed", "error", "failure") or bool(refusal)
        if failed:
            reason = re.sub(r"\s+", " ", str(d.get("message") or d.get("error") or "no reason given"))[:160]
            raise SmartfloError(f"Smartflo did not place the call. {reason}")
        return data

    def hangup(self, token, call_id):
        data = self._call("POST", settings.SMARTFLO_HANGUP_PATH, token, json={"call_id": str(call_id)})
        d = data if isinstance(data, dict) else {}
        log.info("Smartflo hangup replied: %s", {k: d[k] for k in ("success", "status", "message", "error") if k in d and not isinstance(d[k], (dict, list))} or "(no verdict fields)")
        if d.get("success") is False or str(d.get("status", "")).strip().lower() in ("false", "failed", "error", "failure"):
            raise SmartfloError("Smartflo did not end the call. " + re.sub(r"\s+", " ", str(d.get("message") or d.get("error") or ""))[:160])
        return data


def _refusal(data, prefix):
    """Smartflo can answer HTTP 200 with success false and a message ("Agent is on a call."). Turn that into an error."""
    d = data if isinstance(data, dict) else {}
    if d.get("success") is False or str(d.get("status", "")).strip().lower() in ("false", "failed", "error", "failure"):
        reason = re.sub(r"\s+", " ", str(d.get("message") or d.get("error") or ""))[:160]
        raise SmartfloError(f"{prefix} {reason}".strip())


_ID_KEYS = ("call_id", "unique_id", "uuid", "callid", "id")
_NUMBER_KEYS = ("customer_number", "client_number", "destination_number", "destination", "called_number", "callee", "to", "phone", "number", "customer")
_STATE_KEYS = ("status", "call_status", "state")


def _first(d, keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def last10(value):
    d = re.sub(r"\D", "", str(value or ""))
    return d[-10:] if len(d) >= 10 else ""


def _seconds(v):
    """'00:00:16' -> 16 (Smartflo's call_time). Anything else -> 0."""
    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", str(v or "").strip())
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else 0


def call_phase(state):
    """Smartflo's live-call state -> what the agent screen shows. 'Answered' is the one state seen so far; anything that says the
    call is up counts as connected, everything else on a live call is still ringing / dialling."""
    return "connected" if re.search(r"answer|connect|bridge|talk|progress|up", str(state or ""), re.I) else "ringing"


def parse_live_calls(payload):
    """-> (calls, unparsed_keys). Each call is {'id', 'number' (10 digits), 'state'}. unparsed_keys lists field NAMES (never values)
    when the service returned calls in a shape this code does not recognise, so the screen can say so."""
    items = payload
    if isinstance(payload, dict):
        items = None
        for k in ("data", "calls", "results", "live_calls"):
            v = payload.get(k)
            if isinstance(v, list):
                items = v
                break
            if isinstance(v, dict):
                for k2 in ("calls", "data", "results"):
                    if isinstance(v.get(k2), list):
                        items = v[k2]
                        break
        if items is None:
            items = []
    calls, unparsed = [], []
    for it in items:
        if not isinstance(it, dict):
            continue
        cid, num = _first(it, _ID_KEYS), last10(_first(it, _NUMBER_KEYS))
        if cid is None or not num:
            unparsed = sorted(str(k) for k in it.keys())[:30]
            continue
        calls.append({"id": str(cid), "number": num, "state": str(_first(it, _STATE_KEYS) or ""), "seconds": _seconds(it.get("call_time"))})
    return calls, unparsed


def walk_dicts(payload):
    """Every dict inside a Smartflo reply, however it is wrapped. The reply shapes for the set-up calls are not documented."""
    if isinstance(payload, dict):
        yield payload
        for v in payload.values():
            yield from walk_dicts(v)
    elif isinstance(payload, list):
        for v in payload:
            yield from walk_dicts(v)


def find_id(payload, name=None, code=None):
    """The id of the item called `name` (or with `code`) in a reply, or None. Without a name or code: the first 'id' found."""
    for d in walk_dicts(payload):
        if d.get("id") in (None, "") or isinstance(d.get("id"), (dict, list)):
            continue
        if name is None and code is None:
            return str(d["id"])
        if name is not None and str(d.get("name", "")).strip().lower() == name.strip().lower():
            return str(d["id"])
        if code is not None and str(d.get("code", "")).strip().lower() == code.strip().lower():
            return str(d["id"])
    return None
