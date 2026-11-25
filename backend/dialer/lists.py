"""Reading and tidying a Smartflo lead list."""
from time import sleep

from .smartflo import SmartfloError, walk_dicts

MAX_PAGES = 60
LIMIT = 100


def read_list(client, list_id):
    """Every lead in the list: [{'lead_id', 'account', 'calls', 'dial_status', 'available_at'}], and how many pages were read.
    Pages by last_seen_id while Smartflo says there are more. Smartflo answers a lead list as a bare list, with no has_more, so a full page
    (LIMIT leads) is taken to mean there may be more and the next page is asked for from the last lead's id; if Smartflo ignores or refuses
    that, the leads read so far are what is returned and `complete` (third value) is False."""
    seen, out, last, pages, complete = set(), [], None, 0, True
    while pages < MAX_PAGES:
        pages += 1
        try:
            reply = client.list_leads(list_id, last_seen_id=last, limit=LIMIT)
        except SmartfloError:
            if last is None:
                raise
            complete = False
            break
        got = 0
        for d in walk_dicts(reply):
            lid = d.get("lead_id") or d.get("id")
            if lid in (None, "") or "field_2" not in d and "field_0" not in d:
                continue
            if str(lid) in seen:
                continue
            seen.add(str(lid))
            got += 1
            out.append({"lead_id": str(lid), "account": str(d.get("field_2", "")).strip(), "calls": d.get("call_count"), "dial_status": d.get("dial_status"),
                        "available_at": str(d.get("available_at") or "")})
        top = reply if isinstance(reply, dict) else {}
        nxt = top.get("last_seen_id")
        more = top.get("has_more") or top.get("hasMore")
        full_bare = isinstance(reply, list) and len(reply) >= LIMIT       # a bare list that came back full: there may be more
        if full_bare and got:
            nxt, more = out[-1]["lead_id"], True              # ask for the page after its last lead
        if not got or not more or nxt in (None, "") or nxt == last:
            if full_bare:
                complete = False          # Smartflo gave nothing new for the next page, or no way to ask for it
            break
        last = nxt
        sleep(0.3)
    else:
        complete = False
    return out, pages, complete


PURGE_MAX = 60   # leads taken out per start: Smartflo's request limit is shared by every agent, so a long clear-out is spread over starts


def purge_stale(client, list_id, keep_accounts, limit=PURGE_MAX):
    """Take out of an agent's own list every lead that is not one of today's customers (`keep_accounts`, account ids), so a start begins with a
    fresh list. Never raises: if Smartflo is busy the rest is cleared at the next start. Returns how many were removed."""
    try:
        leads, _pages, _complete = read_list(client, list_id)
    except SmartfloError:
        return 0
    removed = 0
    for x in leads:
        if x["account"] in keep_accounts:
            continue
        if removed >= limit:
            break
        try:
            client.delete_lead(x["lead_id"])
        except SmartfloError:
            break
        removed += 1
        sleep(0.25)
    return removed
