"""A record of every call the CRM makes to Smartflo, for support: one log line per call, newest last in the server log.

Each line is `SMARTFLO_API {json}` with the API's name, what was sent and what came back, how long it took and the HTTP status.
Never in the line: tokens, passwords, the Authorization header, or whole phone numbers (only the last 4 digits are kept). Bulk lead
uploads show only how many rows went. Read it with deploy/server/smartflo-api-log.sh, which prints it newest first.
A failure to write the line never stops a call."""
import json
import logging
import re
from datetime import datetime, timezone as dt_tz

from django.conf import settings

log = logging.getLogger("crm.smartflo_api")

_SECRET = re.compile(r"pass|token|secret|authori|apikey|api_key", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?91)?[6-9]\d{5}(\d{4})(?!\d)")
_last_live = {}      # the live-calls answer is asked every few seconds: it is logged only when it changes


def api_name(method, path):
    """The API's plain name for a path, as the Smartflo team knows it."""
    fixed = {"/v1/auth/login": "Agent sign-in", "/v1/auth/refresh": "Token refresh", "/v1/dialer/session/create": "Session create",
             "/v1/dialer/session_call": "Session call (start or end)", "/v1/dialer/logout": "Dialler logout", "/v1/live_calls": "Live calls",
             "/v1/click_to_call": "Click to call", "/v1/call/records": "Call records", "/v1/users": "List users",
             settings.SMARTFLO_DISPOSITION_PATH: "Outcome (disposition) update", settings.SMARTFLO_HANGUP_PATH: "Hang up call"}
    if path in fixed:
        return fixed[path]
    for pattern, name in ((r"^/v1/broadcast/lead/\d+$", "Bulk add leads"), (r"^/v1/leads?", "Leads"), (r"outbound-dialer/add-dialer-campaign", "Create campaign"),
                          (r"^/v1/dialer/campaign", "Campaign"), (r"^/v1/my_number", "My numbers"), (r"disposition", "Outcome list")):
        if re.search(pattern, path):
            return name
    return f"{method} {path}"


def _clean(v):
    if isinstance(v, dict):
        return {k: ("***" if _SECRET.search(str(k)) else _clean(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v[:20]] + ([f"... {len(v) - 20} more"] if len(v) > 20 else [])
    if isinstance(v, str):
        return _PHONE.sub(lambda m: "XXXXXX" + m.group(1), v)[:300]
    return v


def _request_view(path, body, params):
    if re.search(r"^/v1/broadcast/lead/\d+$", path) and isinstance(body, dict):
        rows = body.get("data")
        body = {**{k: v for k, v in body.items() if k != "data"}, "rows_sent": len(rows) if isinstance(rows, list) else 0}
    out = {}
    if body:
        out["body"] = _clean(body)
    if params:
        out["params"] = _clean(params)
    return out


def record(method, path, status, ms, body=None, params=None, response=None, error=""):
    try:
        resp = _clean(response) if response is not None else None
        text = json.dumps(resp, default=str, separators=(",", ":")) if resp is not None else ""
        if method == "GET" and path == "/v1/live_calls" and not error:
            key = (json.dumps(_clean(params), default=str) if params else "", status)
            if _last_live.get(key) == text:
                return
            _last_live[key] = text
        line = {"at": datetime.now(dt_tz.utc).isoformat(timespec="seconds"), "api": api_name(method, path), "method": method, "path": path,
                "status": status, "ms": ms, "request": _request_view(path, body, params), "response": text[:1500] if text else error[:300]}
        log.info("SMARTFLO_API %s", json.dumps(line, default=str, separators=(",", ":")))
    except Exception:           # the record is a convenience; it must never break a call to Smartflo
        pass
