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


def _msg(resp):
    try:
        data = resp.json()
        text = (data.get("message") or data.get("error") or data.get("detail") or "") if isinstance(data, dict) else ""
    except ValueError:
        text = ""
    return re.sub(r"\s+", " ", str(text))[:160]


def _id(v):
    return int(v) if str(v).isdigit() else v


class SmartfloClient:
    def __init__(self, http=None):
        self.http = http or requests

    _prefer = {}  # remembered header form per endpoint, so a fallback is only needed once

    def _send(self, method, path, token, bearer, json, params):
        headers = {"accept": "application/json", "content-type": "application/json",
                   "Authorization": f"Bearer {token}" if bearer else token}
        try:
            r = self.http.request(method, settings.SMARTFLO_API_BASE.rstrip("/") + path, headers=headers, json=json,
                                  params=params, timeout=settings.SMARTFLO_TIMEOUT_SECONDS)
        except requests.RequestException:
            raise SmartfloError("Could not reach Smartflo.")
        if r.status_code >= 400:
            raise SmartfloError(f"Smartflo refused the request ({r.status_code}). {_msg(r)}".strip(), status=r.status_code)
        try:
            return r.json()
        except ValueError:
            return {}

    def _call(self, method, path, token, *, bearer=False, json=None, params=None):
        """The vendor's examples send the token plain on some endpoints and as 'Bearer <token>' on others, and the document
        does not cover every endpoint. If Smartflo rejects the TOKEN (not a wrong password), try the other form once and
        remember which one worked."""
        if not settings.SMARTFLO_ENABLED:
            raise SmartfloDisabled("The dialler is switched off on this server.")
        if not token:
            raise SmartfloError("No Smartflo token is set.")
        key = (method, re.sub(r"\d+", "#", path))
        first = self._prefer.get(key, bearer)
        try:
            data = self._send(method, path, token, first, json, params)
        except SmartfloError as exc:
            if exc.status != 401 or "token" not in str(exc).lower():
                raise
            try:
                data = self._send(method, path, token, not first, json, params)
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

    def probe(self):
        return self._call("GET", "/v1/users", settings.SMARTFLO_API_TOKEN, bearer=True, params={"limit": 1})  # the vendor's example uses Bearer here

    # ---- the agent's own token
    def session_create(self, token, campaign_id, name):
        return self._call("POST", "/v1/dialer/session/create", token, bearer=True,
                          json={"campaign_id": _id(campaign_id), "campaign_name": name})

    def session_call(self, token, campaign_id, start=True):
        return self._call("POST", "/v1/dialer/session_call", token, bearer=True,
                          json={"startOrEnd": bool(start), "campaignId": str(campaign_id), "logout": False})

    def logout(self, token, campaign_id):
        return self._call("POST", "/v1/dialer/logout", token, bearer=True, json={"campaign_id": str(campaign_id)})

    def disposition(self, token, unique_id, status_id):
        return self._call("POST", "/v1/dialer/disposition_details_update", token,
                          json={"disposition_status": str(status_id), "unique_id": str(unique_id)})

    def live_calls(self, token, agent_number):
        return self._call("GET", "/v1/live_calls", token, params={"agent_number": agent_number})

    def click_to_call(self, token, agent_number, destination):
        body = {"agent_number": agent_number, "destination_number": destination, "async": 1}
        if settings.SMARTFLO_CALLER_ID:
            body["caller_id"] = settings.SMARTFLO_CALLER_ID          # the account's outgoing number, as in Smartflo's own example
            body["call_timeout"] = settings.SMARTFLO_CALL_TIMEOUT
        if settings.SMARTFLO_CLICK_TO_CALL_TYPE:
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
