"""One place that decides what a valid mobile number looks like, so the sync and the CRM agree."""
import re


def normalise_mobile(value):
    """Return '91' + 10 digits for a valid Indian mobile, or '' if it is not one."""
    d = re.sub(r"\D", "", str(value or ""))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return "91" + d
    return ""
