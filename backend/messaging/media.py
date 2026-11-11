"""Files in WhatsApp messages: reading what MSG91 says about a photo, document or button reply the customer sent, downloading and
keeping the file, checking a file an agent wants to send, and the short-lived public link MSG91 fetches it from."""
import ipaddress
import json
import mimetypes
import os
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings
from django.core import signing

from . import msg91
from .models import WhatsappMedia

MEDIA_TYPES = ("image", "document", "audio", "video", "sticker", "voice")
_URL_KEYS = ("url", "link", "media_url", "mediaUrl", "file_url", "fileUrl", "download_url", "downloadUrl", "href", "src")
_MIME_KEYS = ("mime_type", "mimeType", "mime", "mimetype")
_NAME_KEYS = ("filename", "file_name", "fileName", "name")
_BUTTON_NODES = ("button_reply", "list_reply", "interactive", "button", "quick_reply", "buttonReply", "listReply")
_LABEL = {"image": "Photo", "document": "Document", "audio": "Voice message", "voice": "Voice message", "video": "Video", "sticker": "Sticker"}

# What may be shown inside the page. Everything else (html, svg, scripts ...) is only ever offered as a download.
INLINE_OK = ("image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf", "video/mp4", "video/3gpp",
             "audio/mpeg", "audio/mp4", "audio/ogg", "audio/aac", "audio/amr", "audio/webm", "audio/wav")
MAX_DOWNLOAD = 16 * 1024 * 1024


def label_for(media_type):
    return _LABEL.get(media_type, "Attachment")


def expand(obj):
    """MSG91 sometimes sends a nested object as a JSON string. Turn those into real objects, all the way down."""
    if isinstance(obj, dict):
        return {k: (v if k in ("body", "caption") else expand(v)) for k, v in obj.items()}   # what a customer typed stays as typed
    if isinstance(obj, list):
        return [expand(v) for v in obj]
    if isinstance(obj, str):
        t = obj.strip()
        if t[:1] in "{[" and t[-1:] in "}]":
            try:
                return expand(json.loads(t))
            except ValueError:
                return obj
    return obj


# ------------------------------------------------------------------------------------------------------------ reading a webhook
def _first(node, keys):
    v = msg91._find(node, keys)
    return v if isinstance(v, (str, int, float)) else None


def inbound_media(payload, ctype):
    """The file a customer message carries: {type, url, mime, name, caption} or None. `payload` has been through expand()."""
    order = ((ctype,) if ctype in MEDIA_TYPES else ()) + tuple(t for t in MEDIA_TYPES if t != ctype)
    for t in order:
        node = msg91._find(payload, (t,))
        if node is None and ctype != t:
            continue
        if isinstance(node, str):
            node = {"url": node} if node.startswith("http") else {}
        if not isinstance(node, dict):
            node = {}
        if ctype == t and not node:
            node = payload if isinstance(payload, dict) else {}
        url = _first(node, _URL_KEYS)
        if not (isinstance(url, str) and url.startswith("http")):
            url = ""
        mtype = "audio" if t == "voice" else t
        return {"type": mtype, "url": url, "mime": str(_first(node, _MIME_KEYS) or "")[:100],
                "name": os.path.basename(str(_first(node, _NAME_KEYS) or ""))[:120], "caption": str(_first(node, ("caption",)) or "").strip()}
    # A bare "media" object with a file type inside it.
    node = msg91._find(payload, ("media", "attachment"))
    if isinstance(node, dict):
        url = _first(node, _URL_KEYS)
        if isinstance(url, str) and url.startswith("http"):
            mime = str(_first(node, _MIME_KEYS) or "")
            kind = (_first(node, ("type", "media_type")) or mime.split("/")[0] or "document")
            kind = str(kind).lower()
            return {"type": kind if kind in MEDIA_TYPES else "document", "url": url, "mime": mime[:100],
                    "name": os.path.basename(str(_first(node, _NAME_KEYS) or ""))[:120], "caption": str(_first(node, ("caption", "text")) or "").strip()}
    return None


def button_text(payload):
    """What the customer tapped: the title of a button or list row they chose ('Yes', 'Pay now' ...)."""
    node = msg91._find(payload, _BUTTON_NODES)
    if isinstance(node, dict):
        inner = node.get("button_reply") or node.get("list_reply") or node.get("buttonReply") or node.get("listReply") or node
        v = _first(inner, ("title", "text", "payload", "id", "value"))
        return str(v).strip() if v else ""
    return str(node).strip() if isinstance(node, (str, int)) and str(node).strip() else ""


def other_text(payload, ctype):
    """Words for the kinds that are neither text, file nor button: a pinned location, a shared contact, a reaction."""
    if ctype == "location":
        loc = msg91._find(payload, ("location",)) or {}
        lat, lng = (_first(loc, ("latitude", "lat")), _first(loc, ("longitude", "lng", "long"))) if isinstance(loc, dict) else (None, None)
        return f"Shared a location ({lat}, {lng})" if lat is not None and lng is not None else "Shared a location"
    if ctype in ("contacts", "contact"):
        return "Shared a contact card"
    if ctype == "reaction":
        v = _first(msg91._find(payload, ("reaction",)) or {}, ("emoji", "text"))
        return f"Reacted {v}" if v else "Reacted to a message"
    if ctype == "order":
        return "Sent an order"
    return f"Sent a {ctype or 'new kind of'} message that the CRM cannot show. Open it in WhatsApp."


# -------------------------------------------------------------------------------------------------------------- downloading
def _public_host(host):
    """Only fetch from the public internet: never our own server, the cloud metadata address or anything on the private network."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def download(url, timeout=(3, 10)):
    """(bytes, mime) of a file at `url`, or None. https only, public hosts only, redirects re-checked, 16 MB at most."""
    for _ in range(4):
        parts = urlparse(url)
        if parts.scheme != "https" or not parts.hostname or not _public_host(parts.hostname):
            return None
        headers = {}
        if parts.hostname.endswith("msg91.com") and settings.MSG91_AUTHKEY:
            headers["authkey"] = settings.MSG91_AUTHKEY
        try:
            with requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    url = urljoin(url, r.headers["Location"])
                    continue
                if r.status_code != 200:
                    return None
                data = b""
                for chunk in r.iter_content(64 * 1024):
                    data += chunk
                    if len(data) > MAX_DOWNLOAD:
                        return None
                return data, (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        except requests.RequestException:
            return None
    return None


_SNIFF = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF8", "image/gif"), (b"%PDF-", "application/pdf"),
          (b"OggS", "audio/ogg"), (b"ID3", "audio/mpeg"))


def sniff_mime(data, declared="", name=""):
    """The real type from the first bytes where we can tell, else what the sender or the file name says."""
    for magic, mime in _SNIFF:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "audio/mp4" if declared.startswith("audio/") else "video/mp4"
    return declared if declared and declared != "application/octet-stream" else (mimetypes.guess_type(name)[0] or "application/octet-stream")


def fetch_inbound(message):
    """Download the file of a customer message once and keep it. True when the CRM now has it. Safe to call again."""
    if WhatsappMedia.objects.filter(message=message).exists():
        return True
    if not message.media_url:
        return False
    got = download(message.media_url)
    if not got:
        return False
    data, declared = got
    mime = sniff_mime(data, message.media_mime or declared, message.media_name)
    WhatsappMedia.objects.create(message=message, content=data, size=len(data))
    message.media_mime = mime[:100]
    if not message.media_name:
        message.media_name = ("whatsapp-" + (message.media_type or "file") + (mimetypes.guess_extension(mime) or ""))[:120]
    message.save(update_fields=["media_mime", "media_name"])
    return True


# --------------------------------------------------------------------------------------------------- a file an agent attaches
IMAGE_MAX = 5 * 1024 * 1024
DOC_MAX = 10 * 1024 * 1024
_DOC_EXT = {".pdf": b"%PDF-", ".doc": b"\xd0\xcf\x11\xe0", ".xls": b"\xd0\xcf\x11\xe0", ".ppt": b"\xd0\xcf\x11\xe0",
            ".docx": b"PK\x03\x04", ".xlsx": b"PK\x03\x04", ".pptx": b"PK\x03\x04", ".txt": b"", ".csv": b""}
_IMG_EXT = {".jpg": (b"\xff\xd8\xff", "image/jpeg"), ".jpeg": (b"\xff\xd8\xff", "image/jpeg"), ".png": (b"\x89PNG\r\n\x1a\n", "image/png")}


class BadFile(Exception):
    pass


def clean_name(name):
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^\w.\- ()]", "_", base)[:100].strip(". ")
    return base or "file"


def check_upload(upload):
    """Returns (media_type, name, mime, bytes) for a file an agent picked, or raises BadFile with a sentence for the agent.
    The kind comes from the file's own first bytes, not from what the browser claims."""
    name = clean_name(getattr(upload, "name", ""))
    ext = os.path.splitext(name)[1].lower()
    data = upload.read()
    if not data:
        raise BadFile("That file is empty.")
    if ext in _IMG_EXT:
        magic, mime = _IMG_EXT[ext]
        if not data.startswith(magic):
            raise BadFile("That does not look like a real picture. Send a JPG or PNG.")
        if len(data) > IMAGE_MAX:
            raise BadFile("Pictures can be 5 MB at most.")
        return "image", name, mime, data
    if ext in _DOC_EXT:
        if not data.startswith(_DOC_EXT[ext]):
            raise BadFile(f"That is not a real {ext[1:].upper()} file.")
        if ext in (".txt", ".csv") and b"\x00" in data[:4096]:
            raise BadFile("That is not a plain text file.")
        if len(data) > DOC_MAX:
            raise BadFile("Documents can be 10 MB at most.")
        return "document", name, mimetypes.guess_type(name)[0] or "application/octet-stream", data
    raise BadFile("WhatsApp attachments can be a JPG or PNG picture, or a PDF, Word, Excel, PowerPoint, text or CSV document.")


# ------------------------------------------------------------------------------------------- the link MSG91 fetches the file from
_SALT = "wa-media"
LINK_HOURS = 6


def public_link(message, base_url):
    """A link that works without a login, for MSG91's servers to fetch one outgoing file. It names only this file and expires."""
    token = signing.TimestampSigner(salt=_SALT).sign(str(message.pk))
    return f"{base_url.rstrip('/')}/api/messaging/whatsapp/media/{message.pk}/public?t={token}"


def link_is_valid(message_id, token):
    try:
        return signing.TimestampSigner(salt=_SALT).unsign(token, max_age=LINK_HOURS * 3600) == str(message_id)
    except signing.BadSignature:
        return False
