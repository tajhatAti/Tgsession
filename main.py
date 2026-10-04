#!/usr/bin/env python3
"""
TgWeb — a single-user, self-hosted, ad-free Telegram web client.

One file: FastAPI backend + embedded HTML/CSS/JS frontend (no build step).
Served under any path prefix (e.g. /live/<slug>/): the page injects <base href>
from location.pathname at runtime and every URL is relative.

  pip install -r requirements.txt
  python main.py

Telegram login (phone + OTP, optional 2FA, or pasting a Telethon
StringSession) happens on the website. The session is persisted to
tgsess.txt and auto-loaded on restart. If it is bad/expired the file is
deleted and the login screen is shown again — the server never crashes.
"""
import asyncio
import base64
import hashlib
import hmac
import html as html_lib
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

# ============================================================================
#  EDIT THESE CONSTANTS (they are yours, kept from your previous version)
# ============================================================================
API_ID = 37109385                                   # https://my.telegram.org
API_HASH = "b50a9ccaf4a0352b895a9fb2998c7f0d"       # https://my.telegram.org
PASSWORD = "Ahad@2026tg"   # site password protecting this app on a public URL
# ============================================================================

SESSION_FILE = "tgsess.txt"
AUTH_COOKIE = "tgw_auth"
AUTH_TTL = 30 * 24 * 3600
AUTH_KEY = hashlib.sha256(b"tgw-auth-v1|" + PASSWORD.encode("utf-8")).digest()
MAX_UPLOAD = 50 * 1024 * 1024

from telethon import TelegramClient, events, functions, types
from telethon import errors as tg_errors
from telethon.errors import FloodWaitError
from telethon.helpers import add_surrogate, del_surrogate, within_surrogate
from telethon.sessions import StringSession
from telethon.utils import get_peer_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tgweb")

# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

_ESC = html_lib.escape


class LRUCache:
    """Thread-unfriendly (single loop) bounded LRU cache for media bytes."""

    def __init__(self, max_items: int, max_bytes: int):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._d: Dict[Any, Tuple[bytes, str]] = {}
        self._bytes = 0

    def get(self, k):
        v = self._d.pop(k, None)
        if v is not None:
            self._d[k] = v
        return v

    def _evict(self, k):
        v = self._d.pop(k, None)
        if v is not None:
            self._bytes -= len(v[0])

    def put(self, k, v: Optional[Tuple[bytes, str]]):
        if v is None:
            return
        if k in self._d:
            self._evict(k)
        self._d[k] = v
        self._bytes += len(v[0])
        while self._d and (self._bytes > self.max_bytes or len(self._d) > self.max_items):
            oldest = next(iter(self._d))
            self._evict(oldest)


thumb_cache = LRUCache(400, 48 * 1024 * 1024)
photo_cache = LRUCache(80, 48 * 1024 * 1024)
avatar_cache = LRUCache(300, 16 * 1024 * 1024)
dl_semaphore = asyncio.Semaphore(4)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def listify(x) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


# ---------------------------------------------------------------------------
# entity -> HTML rendering (own implementation so spoilers are supported
# and no client object is needed; algorithm mirrors telethon's html.unparse)
# ---------------------------------------------------------------------------

def _entity_tags(ent, text_slice: str) -> Optional[Tuple[str, str]]:
    t = type(ent)
    if t is types.MessageEntityBold:
        return ("<strong>", "</strong>")
    if t is types.MessageEntityItalic:
        return ("<em>", "</em>")
    if t is types.MessageEntityCode:
        return ("<code>", "</code>")
    if t is types.MessageEntityPre:
        return ("<pre>", "</pre>")
    if t is types.MessageEntityUnderline:
        return ("<u>", "</u>")
    if t is types.MessageEntityStrike:
        return ("<del>", "</del>")
    if t is types.MessageEntitySpoiler:
        return ('<span class="spoiler">', "</span>")
    if t is types.MessageEntityBlockquote:
        return ("<blockquote>", "</blockquote>")
    if t is types.MessageEntityTextUrl:
        url = getattr(ent, "url", "") or ""
        return ('<a href="%s" target="_blank" rel="noopener noreferrer">' % _ESC(url, quote=True), "</a>")
    if t is types.MessageEntityUrl:
        return ('<a href="%s" target="_blank" rel="noopener noreferrer">' % _ESC(text_slice, quote=True), "</a>")
    if t is types.MessageEntityEmail:
        return ('<a href="mailto:%s">' % _ESC(text_slice, quote=True), "</a>")
    return None  # mentions, hashtags, custom emoji fallback text, etc.


def entities_to_html(text: str, entities) -> str:
    raw = text or ""
    if not raw:
        return ""
    ents = list(entities or [])
    if not ents:
        return _ESC(raw)
    try:
        s = add_surrogate(raw)
        insert_at = []
        for i, ent in enumerate(ents):
            off = getattr(ent, "offset", None)
            ln = getattr(ent, "length", None)
            if off is None or ln is None:
                continue
            a, b = int(off), int(off) + int(ln)
            tags = _entity_tags(ent, s[a:b] if 0 <= a <= b <= len(s) else "")
            if tags:
                insert_at.append((a, i, tags[0]))
                insert_at.append((b, -i, tags[1]))
        if not insert_at:
            return _ESC(raw)
        insert_at.sort(key=lambda t: (t[0], t[1]))
        next_escape_bound = len(s)
        while insert_at:
            at, _, what = insert_at.pop()
            while within_surrogate(s, at):
                at += 1
            s = s[:at] + what + _ESC(s[at:next_escape_bound]) + s[next_escape_bound:]
            next_escape_bound = at
        s = _ESC(s[:next_escape_bound]) + s[next_escape_bound:]
        return del_surrogate(s)
    except Exception as e:  # never let a weird entity break rendering
        log.warning("entities_to_html failed: %s", e)
        return _ESC(raw)


# ---------------------------------------------------------------------------
# serialization helpers (all null-safe: MessageEmpty / service / no sender...)
# ---------------------------------------------------------------------------

def display_name(e) -> str:
    if e is None:
        return ""
    try:
        if isinstance(e, types.User):
            name = " ".join(x for x in (getattr(e, "first_name", None), getattr(e, "last_name", None)) if x)
            return name or getattr(e, "username", None) or ("user%s" % getattr(e, "id", "?"))
        title = getattr(e, "title", None)
        if title:
            return title
        uname = getattr(e, "username", None)
        if uname:
            return "@%s" % uname
        return "chat%s" % getattr(e, "id", "?")
    except Exception:
        return "Unknown"


def user_status_text(e) -> Optional[str]:
    try:
        st = getattr(e, "status", None)
        if isinstance(st, types.UserStatusOnline):
            return "online"
        if isinstance(st, types.UserStatusRecently):
            return "last seen recently"
        if isinstance(st, types.UserStatusLastWeek):
            return "last seen within a week"
        if isinstance(st, types.UserStatusLastMonth):
            return "last seen within a month"
        if isinstance(st, types.UserStatusOffline):
            w = getattr(st, "was_online", None)
            if isinstance(w, datetime):
                return "last seen " + w.strftime("%d %b %Y %H:%M") + " UTC"
        return None
    except Exception:
        return None


def forward_name(f) -> Optional[str]:
    """Best-effort display name of the original sender of a forwarded message."""
    if f is None:
        return None
    try:
        n = getattr(f, "from_name", None)
        if n:
            return n
        s = getattr(f, "sender", None)
        if s is not None:
            return display_name(s)
        c = getattr(f, "chat", None)
        if c is not None:
            return display_name(c)
    except Exception:
        pass
    return None


def sender_json(s) -> Optional[dict]:
    if s is None:
        return None
    try:
        return {"id": getattr(s, "id", 0) or 0, "name": display_name(s), "bot": bool(getattr(s, "bot", False))}
    except Exception:
        return None


def _doc_meta(doc: "types.Document") -> dict:
    name = mime = None
    dur = w = h = None
    try:
        for a in (getattr(doc, "attributes", None) or []):
            if isinstance(a, types.DocumentAttributeFilename):
                name = a.file_name
            elif isinstance(a, types.DocumentAttributeVideo):
                dur, w, h = a.duration, a.w, a.h
            elif isinstance(a, types.DocumentAttributeAudio):
                dur = a.duration
            elif isinstance(a, types.DocumentAttributeImageSize):
                w, h = a.w, a.h
        mime = getattr(doc, "mime_type", None)
    except Exception:
        pass
    return {"name": name, "mime": mime, "size": getattr(doc, "size", None), "duration": dur, "w": w, "h": h}


def _photo_meta(photo: "types.Photo") -> dict:
    size = w = h = None
    try:
        best = None
        for s in (photo.sizes or []):
            if isinstance(s, (types.PhotoSize, types.PhotoSizeProgressive)):
                if isinstance(s, types.PhotoSizeProgressive):
                    sz = max(s.sizes or [0])
                else:
                    sz = s.size or 0
                if best is None or (sz or 0) > best[0]:
                    best = (sz or 0, s.w, s.h)
        if best:
            size, w, h = best[0], best[1], best[2]
    except Exception:
        pass
    return {"size": size, "w": w, "h": h}


def _poll_json(media: "types.MessageMediaPoll") -> dict:
    p = getattr(media, "poll", None)
    question = getattr(p, "question", "") if p else ""
    if not isinstance(question, str):
        question = getattr(question, "text", "") or ""
    answers = []
    try:
        for a in (getattr(p, "answers", None) or []):
            t = getattr(a, "text", "")
            if not isinstance(t, str):
                t = getattr(t, "text", "") or ""
            answers.append({"text": t, "chosen": False, "correct": False, "voters": None})
    except Exception:
        pass
    total = 0
    try:
        res = getattr(media, "results", None)
        total = int(getattr(res, "total_voters", 0) or 0)
        for i, r in enumerate((getattr(res, "results", None) or [])):
            if i < len(answers):
                answers[i]["chosen"] = bool(getattr(r, "chosen", False))
                answers[i]["correct"] = bool(getattr(r, "correct", False))
                answers[i]["voters"] = getattr(r, "voters", None)
    except Exception:
        pass
    return {"kind": "poll", "label": "📊 Poll", "question": question, "answers": answers,
            "total": total, "closed": bool(getattr(p, "closed", False))}


def media_info(m) -> Optional[dict]:
    """Describe a message's media. Returns None if there is none."""
    try:
        if getattr(m, "photo", None) is not None:
            pm = _photo_meta(m.photo)
            return {"kind": "photo", "label": "📷 Photo", "name": None, "mime": "image/jpeg",
                    "size": pm["size"], "duration": None, "w": pm["w"], "h": pm["h"]}
        doc = getattr(m, "document", None)
        if isinstance(doc, types.Document):
            meta = _doc_meta(doc)
            attrs = doc.attributes or []
            is_sticker = any(isinstance(a, types.DocumentAttributeSticker) for a in attrs)
            aud = next((a for a in attrs if isinstance(a, types.DocumentAttributeAudio)), None)
            vid = next((a for a in attrs if isinstance(a, types.DocumentAttributeVideo)), None)
            anim = any(isinstance(a, types.DocumentAttributeAnimated) for a in attrs)
            base = {"name": meta["name"], "mime": meta["mime"], "size": meta["size"],
                    "duration": meta["duration"], "w": meta["w"], "h": meta["h"]}
            if is_sticker:
                alt = ""
                try:
                    st = next(a for a in attrs if isinstance(a, types.DocumentAttributeSticker))
                    alt = st.alt or ""
                except Exception:
                    pass
                return {"kind": "sticker", "label": "%s Sticker" % (alt or "🙂"), "alt": alt, **base}
            if aud is not None and aud.voice:
                return {"kind": "voice", "label": "🎤 Voice message", **base}
            if vid is not None and getattr(vid, "round_message", False):
                return {"kind": "videonote", "label": "📹 Video message", **base}
            if anim:
                return {"kind": "gif", "label": "🎞 GIF", **base}
            if vid is not None:
                return {"kind": "video", "label": "📹 Video", **base}
            if aud is not None:
                return {"kind": "audio", "label": "🎵 Audio",
                        "title": aud.title or meta["name"], "performer": aud.performer, **base}
            if (meta["mime"] or "").startswith("image/"):
                return {"kind": "picfile", "label": "🖼 Image", **base}
            return {"kind": "file", "label": "📎 " + (meta["name"] or "File"), **base}
        media = getattr(m, "media", None)
        if isinstance(media, types.MessageMediaPoll):
            return _poll_json(media)
        if isinstance(media, types.MessageMediaGeo):
            geo = getattr(media, "geo", None)
            return {"kind": "geo", "label": "📍 Location",
                    "lat": getattr(geo, "lat", None), "lon": getattr(geo, "long", None)}
        if isinstance(media, types.MessageMediaContact):
            return {"kind": "contact", "label": "👤 " + (getattr(media, "first_name", "") or "Contact"),
                    "phone": getattr(media, "phone_number", None)}
        if isinstance(media, types.MessageMediaDice):
            return {"kind": "dice", "label": "🎲 Dice", "value": getattr(media, "value", None),
                    "emoji": getattr(media, "emoticon", "🎲")}
        if isinstance(media, types.MessageMediaGame):
            return {"kind": "game", "label": "🎮 Game"}
        if isinstance(media, types.MessageMediaInvoice):
            return {"kind": "invoice", "label": "🧾 Invoice"}
        if isinstance(media, types.MessageMediaUnsupported):
            return {"kind": "unsupported", "label": "⛔ Unsupported media"}
        return None
    except Exception as e:
        log.warning("media_info failed: %s", e)
        return None


def webpage_json(m) -> Optional[dict]:
    try:
        media = getattr(m, "media", None)
        if isinstance(media, types.MessageMediaWebPage):
            wp = getattr(media, "webpage", None)
            if isinstance(wp, types.WebPage):
                has_thumb = getattr(wp, "photo", None) is not None
                return {"url": getattr(wp, "url", None), "title": getattr(wp, "title", None) or "",
                        "desc": getattr(wp, "description", None) or "",
                        "site": getattr(wp, "site_name", None) or "", "thumb": has_thumb}
    except Exception:
        pass
    return None


def reactions_json(m) -> Optional[list]:
    try:
        r = getattr(m, "reactions", None)
        if r is None:
            return None
        out = []
        for rc in (getattr(r, "results", None) or []):
            em = getattr(getattr(rc, "reaction", None), "emoticon", None)
            if not em:
                continue
            out.append({"emoji": em, "count": int(getattr(rc, "count", 0) or 0),
                        "me": getattr(rc, "chosen_order", None) is not None})
        return out or None
    except Exception:
        return None


def service_text(m) -> str:
    try:
        a = getattr(m, "action", None)
        if a is None:
            return "Service message"
        who = ""
        try:
            s = m.sender
        except Exception:
            s = None
        if s is not None:
            who = display_name(s) + " "
        if isinstance(a, types.MessageActionChatCreate):
            return who + "created the group"
        if isinstance(a, types.MessageActionChatEditTitle):
            return who + "changed the group title"
        if isinstance(a, types.MessageActionChatEditPhoto):
            return who + "updated the group photo"
        if isinstance(a, types.MessageActionChatDeletePhoto):
            return who + "removed the group photo"
        if isinstance(a, types.MessageActionChatAddUser):
            return who + "added a member"
        if isinstance(a, types.MessageActionChatDeleteUser):
            return who + "removed a member"
        if isinstance(a, types.MessageActionChatJoinedByLink):
            return who + "joined via invite link"
        if isinstance(a, types.MessageActionPinMessage):
            return who + "pinned a message"
        if isinstance(a, types.MessageActionHistoryClear):
            return "History cleared"
        if isinstance(a, types.MessageActionScreenshotTaken):
            return who + "took a screenshot"
        if isinstance(a, types.MessageActionPhoneCall):
            dur = getattr(a, "duration", None)
            return ("Phone call (%ds)" % dur) if dur else "Phone call"
        if isinstance(a, types.MessageActionContactSignUp):
            return who + "joined Telegram"
        if isinstance(a, types.MessageActionChatMigrateTo):
            return "Group upgraded to supergroup"
        if isinstance(a, types.MessageActionChannelMigrateFrom):
            return "Channel migrated from group"
        if isinstance(a, types.MessageActionSetMessagesTTL):
            return "Messages auto-delete enabled"
        if isinstance(a, types.MessageActionGroupCall):
            return "Group call started"
        return "Service message"
    except Exception:
        return "Service message"


def message_html(m) -> str:
    if getattr(m, "action", None) is not None:
        return _ESC(service_text(m))
    try:
        return entities_to_html(getattr(m, "message", None) or "", getattr(m, "entities", None))
    except Exception:
        return _ESC(getattr(m, "message", None) or "")


def snippet_text(m, limit: int = 120) -> str:
    if m is None:
        return ""
    try:
        if getattr(m, "action", None) is not None:
            return service_text(m)[:limit]
        txt = (getattr(m, "message", None) or "").strip()
        if txt:
            return re.sub(r"\s+", " ", txt)[:limit]
        mi = media_info(m)
        if mi:
            return (mi.get("label") or "Media")[:limit]
        if webpage_json(m):
            return "🔗 Link"
        return ""
    except Exception:
        return ""


def message_to_json(m, chat_id: Optional[int] = None) -> Optional[dict]:
    if m is None or isinstance(m, types.MessageEmpty):
        return None
    try:
        mid = int(getattr(m, "id", 0) or 0)
    except Exception:
        return None
    try:
        sender = None
        try:
            sender = m.sender
        except Exception:
            sender = None
        fwd = None
        try:
            fwd = forward_name(m.forward)
        except Exception:
            fwd = None
        is_service = getattr(m, "action", None) is not None
        return {
            "id": mid,
            "chat_id": chat_id,
            "date": iso(getattr(m, "date", None)),
            "out": bool(getattr(m, "out", False)),
            "sender": sender_json(sender),
            "html": message_html(m),
            "raw": getattr(m, "message", None) or "",
            "service": is_service,
            "reply_to": None,
            "media": None if is_service else media_info(m),
            "webpage": None if is_service else webpage_json(m),
            "reactions": reactions_json(m),
            "edited": bool(getattr(m, "edit_date", None)),
            "views": getattr(m, "views", None),
            "forward_from": fwd,
        }
    except Exception as e:
        log.warning("failed to serialize message %s: %s", mid, e)
        return {"id": mid, "chat_id": chat_id, "date": None, "out": False, "sender": None,
                "html": "", "raw": "", "service": True, "reply_to": None, "media": None,
                "webpage": None, "reactions": None, "edited": False, "views": None,
                "forward_from": None, "error": True}


def reply_info(m, local: Dict[int, Any], fetched: Dict[int, Any]) -> Optional[dict]:
    try:
        rid = m.reply_to_msg_id
    except Exception:
        rid = None
    if not rid:
        return None
    r = local.get(rid) or fetched.get(rid)
    if r is None:
        return {"id": rid, "name": None, "snippet": None}
    name = None
    try:
        s = r.sender
    except Exception:
        s = None
    if s is not None:
        name = display_name(s)
    return {"id": rid, "name": name, "snippet": snippet_text(r, 90) or None}


async def serialize_messages(client, entity, msgs, chat_id: Optional[int]) -> List[dict]:
    msgs = [m for m in msgs if m is not None and not isinstance(m, types.MessageEmpty)]
    fetched: Dict[int, Any] = {}
    try:
        want = set()
        local = {}
        for m in msgs:
            local[int(getattr(m, "id", 0) or 0)] = m
            try:
                rid = m.reply_to_msg_id
            except Exception:
                rid = None
            if rid:
                want.add(int(rid))
        to_fetch = [i for i in want if i not in local]
        for i in range(0, len(to_fetch), 100):
            chunk = to_fetch[i:i + 100]
            try:
                rs = listify(await client.get_messages(entity, ids=chunk))
            except Exception as e:
                log.warning("reply lookup failed: %s", e)
                rs = []
            for r in rs:
                if r is not None and not isinstance(r, types.MessageEmpty):
                    fetched[int(getattr(r, "id", 0) or 0)] = r
        out = []
        for m in msgs:
            j = message_to_json(m, chat_id)
            if j is None:
                continue
            j["reply_to"] = reply_info(m, local, fetched)
            out.append(j)
        return out
    except Exception as e:
        log.warning("serialize_messages degraded: %s", e)
        return [j for j in (message_to_json(m, chat_id) for m in msgs) if j is not None]


def dialog_to_json(d) -> Optional[dict]:
    try:
        e = getattr(d, "entity", None)
        etype = "chat"
        is_bot = is_self = verified = False
        if isinstance(e, types.User):
            is_bot = bool(e.bot)
            is_self = bool(getattr(e, "self", False) or getattr(e, "is_self", False))
            verified = bool(getattr(e, "verified", False))
            etype = "bot" if is_bot else "user"
        elif isinstance(e, types.Chat):
            etype = "group"
            verified = bool(getattr(e, "verified", False))
        elif isinstance(e, types.Channel):
            etype = "channel" if e.broadcast else "group"
            verified = bool(getattr(e, "verified", False))
        did = int(getattr(d, "id", 0) or 0)
        raw_dialog = getattr(d, "dialog", None)
        unread = int(getattr(d, "unread_count", 0) or 0)
        mentions = int(getattr(d, "mention_count", 0) or 0)
        pinned = bool(getattr(d, "pinned", False))
        archived = bool(getattr(d, "archived", False))
        muted = False
        try:
            ns = getattr(raw_dialog, "notify_settings", None)
            mu = getattr(ns, "mute_until", None) if ns is not None else None
            if isinstance(mu, datetime):
                muted = mu.year >= 2100 or mu > datetime.now(timezone.utc)
            elif isinstance(mu, int) and mu:
                muted = mu == -1 or mu >= 2147483000 or mu > time.time()
        except Exception:
            pass
        name = display_name(e) if e is not None else (getattr(d, "name", None) or "Unknown")
        if is_self:
            name = "Saved Messages"
        last = None
        try:
            lm = getattr(d, "message", None)
        except Exception:
            lm = None
        if lm is not None:
            sname = None
            if getattr(lm, "action", None) is None:
                try:
                    s = lm.sender
                except Exception:
                    s = None
                if s is not None:
                    sname = display_name(s)
            last = {"id": getattr(lm, "id", None), "snippet": snippet_text(lm),
                    "date": iso(getattr(lm, "date", None)),
                    "out": bool(getattr(lm, "out", False)), "sender": sname}
        return {
            "id": did,
            "type": etype,
            "name": name,
            "username": getattr(e, "username", None) if e is not None else None,
            "is_self": is_self, "is_bot": is_bot, "verified": verified,
            "unread": unread, "mentions": mentions,
            "pinned": pinned, "archived": archived, "muted": muted,
            "has_photo": getattr(e, "photo", None) is not None if e is not None else False,
            "last": last,
            "status": user_status_text(e) if isinstance(e, types.User) and not is_bot else None,
            "members": getattr(e, "participants_count", None) if isinstance(e, (types.Chat, types.Channel)) else None,
            "read_inbox_max_id": int(getattr(raw_dialog, "read_inbox_max_id", 0) or 0) if raw_dialog is not None else 0,
            "read_outbox_max_id": int(getattr(raw_dialog, "read_outbox_max_id", 0) or 0) if raw_dialog is not None else 0,
        }
    except Exception as ex:
        log.warning("failed to serialize dialog: %s", ex)
        return None


def participant_json(u) -> Optional[dict]:
    try:
        if u is None:
            return None
        role = ""
        p = getattr(u, "participant", None)
        if isinstance(p, (types.ChannelParticipantCreator, types.ChatParticipantCreator)):
            role = "owner"
        elif isinstance(p, (types.ChannelParticipantAdmin, types.ChatParticipantAdmin)):
            role = "admin"
        return {"id": int(getattr(u, "id", 0) or 0), "name": display_name(u),
                "username": getattr(u, "username", None), "bot": bool(getattr(u, "bot", False)),
                "status": user_status_text(u), "role": role}
    except Exception:
        return None

# ---------------------------------------------------------------------------
# site auth (HMAC-signed httpOnly cookie)
# ---------------------------------------------------------------------------

def make_cookie_value() -> str:
    exp = int(time.time()) + AUTH_TTL
    sig = hmac.new(AUTH_KEY, str(exp).encode(), hashlib.sha256).hexdigest()
    return "%d.%s" % (exp, sig)


def cookie_ok(request: Request) -> bool:
    raw = request.cookies.get(AUTH_COOKIE)
    if not raw or "." not in raw:
        return False
    exp_s, _, sig = raw.rpartition(".")
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    good = hmac.new(AUTH_KEY, exp_s.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    return exp > time.time()


def secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


async def require_site(request: Request) -> None:
    if not cookie_ok(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ---------------------------------------------------------------------------
# Telegram client state (single client, single event loop)
# ---------------------------------------------------------------------------

class TGState:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.authorized = False
        self.me: Optional[dict] = None
        self.me_id: Optional[int] = None
        self.phone: Optional[str] = None
        self.phone_code_hash: Optional[str] = None
        self.last_error: Optional[str] = None
        self.lock = asyncio.Lock()
        self.typing: Dict[int, Dict[int, float]] = {}   # chat id -> {user id: expire ts}
        self.names: Dict[int, str] = {}                 # user id -> display name

    async def reset(self):
        client, self.client = self.client, None
        self.authorized = False
        self.me = None
        self.me_id = None
        self.phone = None
        self.phone_code_hash = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    def finish_login(self, client, me):
        self.client = client
        self.authorized = True
        if me is not None:
            self.me = {"id": getattr(me, "id", None), "name": display_name(me),
                       "username": getattr(me, "username", None)}
            self.me_id = getattr(me, "id", None)
        self.phone = None
        self.phone_code_hash = None
        self.last_error = None
        save_session_string(client)
        register_update_handlers(client)


state = TGState()


async def _safe_disconnect(client):
    try:
        if client is not None:
            await client.disconnect()
    except Exception:
        pass


def _remove_session_file():
    try:
        os.remove(SESSION_FILE)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("could not remove session file: %s", e)


def save_session_string(client) -> bool:
    try:
        s = client.session.save()
        if s:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                f.write(s)
        return True
    except Exception as e:
        log.error("could not save session file: %s", e)
        return False


async def require_client() -> TelegramClient:
    if state.client is None or not state.authorized:
        raise HTTPException(status_code=409, detail="Telegram is not logged in")
    return state.client


async def resolve_entity(client, chat_id: int):
    try:
        return await client.get_input_entity(chat_id)
    except (HTTPException, FloodWaitError, tg_errors.RPCError):
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Chat not found — refresh the dialog list")


# ---------------------------------------------------------------------------
# incoming typing events (polled by the frontend with /api/messages)
# ---------------------------------------------------------------------------

def register_update_handlers(client):
    if getattr(client, "_tgw_handlers", False):
        return
    client._tgw_handlers = True

    async def on_update(update):
        try:
            now = time.time()
            if isinstance(update, types.UpdateUserTyping):
                cid, uid = update.user_id, update.user_id
            elif isinstance(update, types.UpdateChatUserTyping):
                cid = get_peer_id(types.PeerChat(update.chat_id))
                uid = update.from_id.user_id if isinstance(update.from_id, types.PeerUser) else None
            elif isinstance(update, types.UpdateChannelUserTyping):
                cid = get_peer_id(types.PeerChannel(update.channel_id))
                uid = update.from_id.user_id if isinstance(update.from_id, types.PeerUser) else None
            else:
                return
            if uid is None:
                return
            state.typing.setdefault(cid, {})[uid] = now + 6
            if uid not in state.names:
                try:
                    u = await client.get_entity(uid)
                    if u is not None:
                        state.names[uid] = display_name(u)
                except Exception:
                    state.names[uid] = "someone"
        except Exception:
            pass

    try:
        client.add_event_handler(on_update, events.Raw([
            types.UpdateUserTyping,
            types.UpdateChatUserTyping,
            types.UpdateChannelUserTyping,
        ]))
    except Exception as e:
        log.warning("could not register update handlers: %s", e)


def typing_names(chat_id: int) -> List[str]:
    now = time.time()
    out = []
    users = state.typing.get(chat_id)
    if not users:
        return out
    for uid, until in list(users.items()):
        if until < now:
            users.pop(uid, None)
            continue
        out.append(state.names.get(uid) or "someone")
    if not users:
        state.typing.pop(chat_id, None)
    return out


# ---------------------------------------------------------------------------
# startup: auto-login from tgsess.txt, never crash
# ---------------------------------------------------------------------------

async def init_telegram():
    for attempt in range(5):
        if not os.path.exists(SESSION_FILE):
            return
        try:
            s = ""
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if not s:
                _remove_session_file()
                return
            try:
                sess = StringSession(s)
            except Exception:
                log.warning("session file is corrupt — removing it")
                _remove_session_file()
                state.last_error = "The saved session was corrupt — please log in again."
                return
            client = TelegramClient(sess, API_ID, API_HASH)
            try:
                await client.connect()
            except tg_errors.AuthKeyDuplicatedError:
                await _safe_disconnect(client)
                _remove_session_file()
                state.last_error = ("This session is already running from another IP address "
                                    "(AuthKeyDuplicated). Log out there or paste a fresh session string.")
                log.warning("auth key duplicated — session file removed")
                return
            try:
                if not await client.is_user_authorized():
                    await _safe_disconnect(client)
                    _remove_session_file()
                    state.last_error = "The saved session is no longer authorized — please log in again."
                    log.warning("saved session not authorized — file removed")
                    return
                me = await client.get_me()
                state.finish_login(client, me)
                state.last_error = None
                log.info("auto-login OK as %s", state.me["name"] if state.me else "?")
            except tg_errors.AuthKeyDuplicatedError:
                await _safe_disconnect(client)
                _remove_session_file()
                state.last_error = ("This session is already running from another IP address "
                                    "(AuthKeyDuplicated). Log out there or paste a fresh session string.")
                return
            # warm up entity caches in the background
            try:
                async for _d in client.iter_dialogs(limit=100):
                    pass
            except Exception:
                pass
            return
        except Exception as e:
            log.warning("connect attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(min(60, 10 * (attempt + 1)))
    state.last_error = "Could not connect to Telegram — the session file was kept; restart to retry."


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(init_telegram())
    try:
        yield
    finally:
        task.cancel()
        if state.client is not None:
            try:
                await asyncio.wait_for(state.client.disconnect(), timeout=5)
            except Exception:
                pass


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class PrefixStripMiddleware:
    """The app may be reverse-proxied under an unknown path prefix such as
    /live/<slug>/. The page computes its own <base href>, so requests arrive
    with the full prefixed path; strip everything before the known route."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path") or "/"
            new_path = None
            if not path.startswith("/api/") and path != "/api" \
                    and not path.startswith(("/healthz", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png")):
                if path.endswith("/"):
                    new_path = "/"            # any page path -> index (API routes never end in /)
                else:
                    idx = path.rfind("/api/")
                    last = path.rsplit("/", 1)[-1]
                    if idx > 0:
                        new_path = path[idx:]
                    elif path.endswith("/manifest.webmanifest"):
                        new_path = "/manifest.webmanifest"
                    elif path.endswith("/icon-192.png"):
                        new_path = "/icon-192.png"
                    elif path.endswith("/icon-512.png"):
                        new_path = "/icon-512.png"
                    elif path.endswith("/healthz"):
                        new_path = "/healthz"
                    elif "." not in last:
                        new_path = "/"        # page path without trailing slash
                if new_path is not None and new_path != path:
                    scope["path"] = new_path
                    try:
                        scope["raw_path"] = new_path.encode()
                    except Exception:
                        pass
        await self.app(scope, receive, send)


app.add_middleware(PrefixStripMiddleware)


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------

class PasswordBody(BaseModel):
    password: str = ""


class PhoneBody(BaseModel):
    phone: str = ""


class CodeBody(BaseModel):
    code: str = ""


class SessionBody(BaseModel):
    session: str = ""


class SendBody(BaseModel):
    chat_id: int
    text: str = ""
    reply_to: Optional[int] = None


class EditBody(BaseModel):
    chat_id: int
    msg_id: int
    text: str = ""


class DeleteBody(BaseModel):
    chat_id: int
    msg_ids: List[int]
    revoke: bool = True


class ForwardBody(BaseModel):
    from_chat_id: int
    msg_ids: List[int]
    to_chat_id: int


class ReadBody(BaseModel):
    chat_id: int
    max_id: Optional[int] = None


class TypingBody(BaseModel):
    chat_id: int


class ReactBody(BaseModel):
    chat_id: int
    msg_id: int
    emoji: str = ""


class PinBody(BaseModel):
    chat_id: int
    msg_id: int
    pinned: bool = True


class MuteBody(BaseModel):
    chat_id: int
    muted: bool = True


class ArchiveBody(BaseModel):
    chat_id: int
    archived: bool = True


# ---------------------------------------------------------------------------
# public routes (no site auth)
# ---------------------------------------------------------------------------

@app.post("/api/login")
async def api_login(body: PasswordBody, request: Request):
    got = hmac.new(AUTH_KEY, (body.password or "").encode("utf-8"), hashlib.sha256).digest()
    want = hmac.new(AUTH_KEY, PASSWORD.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(got, want):
        raise HTTPException(status_code=401, detail="Wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(AUTH_COOKIE, make_cookie_value(), max_age=AUTH_TTL, httponly=True,
                    samesite="lax", secure=secure_cookie(request), path="/")
    return resp


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ---------------------------------------------------------------------------
# authenticated API
# ---------------------------------------------------------------------------

api = APIRouter(prefix="/api", dependencies=[Depends(require_site)])


@api.get("/status")
async def api_status():
    return {"tg": state.authorized, "me": state.me, "error": state.last_error}


@api.post("/site_logout")
async def api_site_logout(request: Request):
    if not cookie_ok(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


# ---- Telegram login -------------------------------------------------------

@api.post("/tg/login_phone")
async def tg_login_phone(body: PhoneBody):
    phone = (body.phone or "").strip()
    if not phone:
        raise HTTPException(400, "Enter a phone number with country code, e.g. +8801XXXXXXXXX")
    if state.authorized:
        raise HTTPException(409, "Already logged in — log out first")
    async with state.lock:
        await state.reset()
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        state.client = client
        try:
            await client.connect()
            result = await client.send_code_request(phone)
        except tg_errors.ApiIdInvalidError:
            raise HTTPException(400, "API_ID / API_HASH is invalid — check the constants at the top of main.py")
        except tg_errors.FloodWaitError as e:
            raise HTTPException(429, "Telegram says: wait %ss before retrying" % e.seconds,
                                headers={"Retry-After": str(e.seconds)})
        except tg_errors.RPCError as e:
            raise HTTPException(400, "Telegram error: %s" % e)
        state.phone = phone
        state.phone_code_hash = result.phone_code_hash
        return {"ok": True, "phone": phone}


@api.post("/tg/login_code")
async def tg_login_code(body: CodeBody):
    client = state.client
    if client is None or not state.phone:
        raise HTTPException(409, "Request a login code first")
    code = re.sub(r"[^\d]", "", body.code or "")
    try:
        me = await client.sign_in(phone=state.phone, code=code, phone_code_hash=state.phone_code_hash)
    except tg_errors.SessionPasswordNeededError:
        return {"ok": False, "need_password": True}
    except (tg_errors.PhoneCodeInvalidError, tg_errors.PhoneCodeEmptyError):
        raise HTTPException(400, "That code is not valid")
    except tg_errors.PhoneCodeExpiredError:
        raise HTTPException(400, "The code has expired — request a new one")
    except tg_errors.PhoneNumberUnoccupiedError:
        raise HTTPException(400, "This number is not registered on Telegram")
    state.finish_login(client, me)
    return {"ok": True, "me": state.me}


@api.post("/tg/login_password")
async def tg_login_password(body: PasswordBody):
    client = state.client
    if client is None:
        raise HTTPException(409, "Request a login code first")
    try:
        me = await client.sign_in(password=body.password or "")
    except tg_errors.PasswordHashInvalidError:
        raise HTTPException(400, "Wrong two-factor password")
    except tg_errors.FloodWaitError as e:
        raise HTTPException(429, "Telegram says: wait %ss before retrying" % e.seconds,
                            headers={"Retry-After": str(e.seconds)})
    state.finish_login(client, me)
    return {"ok": True, "me": state.me}


@api.post("/tg/import_session")
async def tg_import_session(body: SessionBody):
    s = (body.session or "").strip()
    if not s:
        raise HTTPException(400, "Paste a Telethon StringSession first")
    try:
        sess = StringSession(s)
    except Exception:
        raise HTTPException(400, "That does not look like a valid session string")
    tmp = TelegramClient(sess, API_ID, API_HASH)
    try:
        await tmp.connect()
        if not await tmp.is_user_authorized():
            await _safe_disconnect(tmp)
            raise HTTPException(400, "This session is valid but not logged in — generate a fresh one")
        me = await tmp.get_me()
    except HTTPException:
        raise
    except tg_errors.AuthKeyDuplicatedError:
        await _safe_disconnect(tmp)
        raise HTTPException(409, "This session is already in use from another IP (AuthKeyDuplicated). "
                                 "Log out there, or create a fresh session string.")
    except (tg_errors.AuthKeyUnregisteredError, tg_errors.AuthKeyError):
        await _safe_disconnect(tmp)
        raise HTTPException(400, "This session has expired or been revoked — create a new session string")
    except tg_errors.ApiIdInvalidError:
        await _safe_disconnect(tmp)
        raise HTTPException(400, "API_ID / API_HASH is invalid — check the constants at the top of main.py")
    except (tg_errors.RPCError, ValueError, OSError) as e:
        await _safe_disconnect(tmp)
        raise HTTPException(400, "Could not connect with this session: %s" % e)
    await state.reset()
    state.finish_login(tmp, me)
    return {"ok": True, "me": state.me}


@api.post("/tg/logout")
async def tg_logout():
    client = state.client
    state.client = None
    state.authorized = False
    state.me = None
    state.me_id = None
    state.last_error = None
    state.phone = None
    state.phone_code_hash = None
    ok = False
    if client is not None:
        try:
            ok = await client.log_out()
        except Exception as e:
            log.warning("log_out failed: %s", e)
            await _safe_disconnect(client)
    _remove_session_file()
    return {"ok": True, "logged_out": bool(ok)}


# ---- dialogs ---------------------------------------------------------------

@api.get("/dialogs")
async def api_dialogs():
    client = await require_client()
    out = []

    async def collect(folder, limit):
        try:
            async for d in client.iter_dialogs(folder=folder, limit=limit):
                j = dialog_to_json(d)
                if j is not None:
                    out.append(j)
        except Exception as e:
            log.warning("iter_dialogs(folder=%s) failed: %s", folder, e)

    await collect(0, 400)
    await collect(1, 100)

    def sort_key(dj):
        dt = (dj.get("last") or {}).get("date")
        t = 0.0
        if dt:
            try:
                t = datetime.fromisoformat(dt).timestamp()
            except Exception:
                t = 0.0
        return (dj.get("pinned", False), t)

    out.sort(key=sort_key, reverse=True)
    return {"dialogs": out, "me": state.me}


# ---- messages ---------------------------------------------------------------

async def peer_dialog_info(client, entity) -> Optional[dict]:
    try:
        res = await client(functions.messages.GetPeerDialogsRequest(
            peers=[types.InputDialogPeer(peer=entity)]))
        d = res.dialogs[0] if getattr(res, "dialogs", None) else None
        if d is None:
            return None
        return {
            "read_inbox_max_id": int(getattr(d, "read_inbox_max_id", 0) or 0),
            "read_outbox_max_id": int(getattr(d, "read_outbox_max_id", 0) or 0),
            "unread_count": int(getattr(d, "unread_count", 0) or 0),
        }
    except Exception as e:
        log.warning("GetPeerDialogs failed: %s", e)
        return None


@api.get("/messages")
async def api_messages(chat_id: int, offset_id: int = 0, limit: int = Query(50, ge=1, le=100),
                       min_id: int = 0, anchor_id: int = 0):
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    kw = dict(limit=limit)
    if anchor_id:
        kw["offset_id"] = int(anchor_id) + 1   # include the anchor message
    elif offset_id:
        kw["offset_id"] = offset_id
    if min_id:
        kw["min_id"] = min_id
    try:
        msgs = listify(await client.get_messages(entity, **kw))
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    data = await serialize_messages(client, entity, msgs, chat_id)
    info = await peer_dialog_info(client, entity)
    return {"messages": data, "typing": typing_names(chat_id), **(info or {})}


@api.get("/search")
async def api_search(chat_id: int, q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=100)):
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    msgs = listify(await client.get_messages(entity, search=q, limit=limit))
    data = await serialize_messages(client, entity, msgs, chat_id)
    return {"messages": data}


GALLERY_FILTERS = {
    "media": types.InputMessagesFilterPhotoVideo,
    "files": types.InputMessagesFilterDocument,
    "voice": types.InputMessagesFilterVoice,
    "links": types.InputMessagesFilterUrl,
    "music": types.InputMessagesFilterMusic,
}


@api.get("/gallery")
async def api_gallery(chat_id: int, tab: str = "media",
                      limit: int = Query(60, ge=1, le=100), offset_id: int = 0):
    flt = GALLERY_FILTERS.get(tab)
    if flt is None:
        raise HTTPException(400, "Unknown gallery tab")
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    kw = dict(limit=limit, filter=flt)
    if offset_id:
        kw["offset_id"] = offset_id
    msgs = listify(await client.get_messages(entity, **kw))
    data = await serialize_messages(client, entity, msgs, chat_id)
    return {"messages": data}


# ---- members ----------------------------------------------------------------

@api.get("/members")
async def api_members(chat_id: int, q: str = "", limit: int = Query(200, ge=1, le=500)):
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    out: List[dict] = []
    note = None

    async def collect(it):
        async for u in it:
            j = participant_json(u)
            if j is not None:
                out.append(j)
            if len(out) >= limit:
                break

    try:
        await collect(client.iter_participants(entity, limit=limit, search=(q or None)))
    except Exception as e:
        log.warning("iter_participants failed: %s", e)
        try:
            await collect(client.iter_participants(entity, limit=50,
                                                   filter=types.ChannelParticipantsAdmins))
            note = "Only admins are visible in this chat"
        except Exception:
            note = "Participant list is not available for this chat"
    return {"members": out, "note": note}


# ---- sending / editing / deleting -------------------------------------------

@api.post("/send")
async def api_send(body: SendBody):
    text = body.text or ""
    if not text.strip():
        raise HTTPException(400, "Message text is empty")
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    reply = body.reply_to or None
    try:
        msg = await client.send_message(entity, text, reply_to=reply,
                                        parse_mode="html", link_preview=True)
    except ValueError:
        # user typed something that is not valid HTML — send it verbatim
        msg = await client.send_message(entity, text, reply_to=reply, parse_mode=())
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    data = await serialize_messages(client, entity, [msg], body.chat_id)
    return {"message": data[0] if data else None}


@api.post("/edit")
async def api_edit(body: EditBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    try:
        msg = await client.edit_message(entity, body.msg_id, body.text or "", parse_mode="html")
    except ValueError:
        msg = await client.edit_message(entity, body.msg_id, body.text or "", parse_mode=())
    except tg_errors.MessageNotModifiedError:
        raise HTTPException(400, "The message was not changed")
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    data = await serialize_messages(client, entity, listify(msg), body.chat_id)
    return {"message": data[0] if data else None}


@api.post("/delete")
async def api_delete(body: DeleteBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    ids = [i for i in body.msg_ids if i]
    if not ids:
        raise HTTPException(400, "No messages selected")
    try:
        await client.delete_messages(entity, ids, revoke=body.revoke)
    except tg_errors.RPCError:
        try:
            await client.delete_messages(entity, ids, revoke=False)  # delete for me only
        except tg_errors.RPCError as e:
            raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@api.post("/forward")
async def api_forward(body: ForwardBody):
    client = await require_client()
    src = await resolve_entity(client, body.from_chat_id)
    dst = await resolve_entity(client, body.to_chat_id)
    ids = [i for i in body.msg_ids if i]
    if not ids:
        raise HTTPException(400, "No messages selected")
    try:
        await client.forward_messages(dst, ids, from_peer=src)
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@api.post("/read")
async def api_read(body: ReadBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    try:
        await client.send_read_acknowledge(entity, max_id=body.max_id or None, clear_mentions=True)
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@api.post("/typing")
async def api_typing(body: TypingBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    try:
        await client(functions.messages.SetTypingRequest(
            peer=entity, action=types.SendMessageTypingAction()))
    except tg_errors.RPCError:
        pass  # typing is best-effort
    return {"ok": True}


@api.post("/react")
async def api_react(body: ReactBody):
    if not hasattr(functions.messages, "SendReactionRequest"):
        raise HTTPException(501, "Reactions need a newer Telethon")
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    reaction = [types.ReactionEmoji(emoticon=body.emoji)] if body.emoji else []
    await client(functions.messages.SendReactionRequest(
        peer=entity, msg_id=body.msg_id, reaction=reaction))
    msgs = listify(await client.get_messages(entity, ids=[body.msg_id]))
    data = await serialize_messages(client, entity, msgs, body.chat_id)
    return {"message": data[0] if data else None}


@api.post("/pin")
async def api_pin(body: PinBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    try:
        if body.pinned:
            await client.pin_message(entity, body.msg_id, notify=False)
        else:
            await client.unpin_message(entity, body.msg_id)
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@api.post("/mute")
async def api_mute(body: MuteBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    until = 2147483647 if body.muted else 0
    await client(functions.account.UpdateNotifySettingsRequest(
        peer=types.InputNotifyPeer(peer=entity),
        settings=types.InputPeerNotifySettings(mute_until=until)))
    return {"ok": True}


@api.post("/archive")
async def api_archive(body: ArchiveBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    await client(functions.folders.EditPeerFoldersRequest(
        folder_peers=[types.InputFolderPeer(peer=entity, folder_id=1 if body.archived else 0)]))
    return {"ok": True}


# ---- upload ------------------------------------------------------------------

@api.post("/upload")
async def api_upload(chat_id: int = Form(...), caption: str = Form(""), file: UploadFile = File(...)):
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    data = await file.read(MAX_UPLOAD + 1)
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (limit is 50 MB)")
    try:
        msg = await client.send_file(entity, data, file_name=file.filename or "file",
                                     caption=(caption or None), force_document=False)
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    data_j = await serialize_messages(client, entity, [msg], chat_id)
    return {"message": data_j[0] if data_j else None}


# ---- avatars -------------------------------------------------------------------

@api.get("/avatar/{chat_id}")
async def api_avatar(chat_id: int):
    client = await require_client()
    key = ("a", chat_id)
    hit = avatar_cache.get(key)
    if hit is not None:
        if not hit[0]:
            raise HTTPException(404, "No photo")
        return _bytes_response(hit, "private, max-age=86400")
    try:
        entity = await resolve_entity(client, chat_id)
    except HTTPException:
        raise
    async with dl_semaphore:
        try:
            data = await client.download_profile_photo(entity, file=bytes, download_big=False)
        except Exception as e:
            log.warning("avatar download failed (%s): %s", chat_id, e)
            data = None
    if not data:
        avatar_cache.put(key, (b"", ""))
        raise HTTPException(404, "No photo")
    val = (bytes(data), _sniff_image(data))
    avatar_cache.put(key, val)
    return _bytes_response(val, "private, max-age=86400")


def _bytes_response(val: Tuple[bytes, str], cache_control: str) -> Response:
    data, mime = val
    return Response(data, media_type=mime or "application/octet-stream",
                    headers={"Cache-Control": cache_control, "X-Content-Type-Options": "nosniff"})


def _sniff_image(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


# ---- media (thumbnails, photos, Range-streamed files) --------------------------

def parse_range_header(header: Optional[str], size: int):
    """Return (start, end) inclusive, None to ignore the header, raise ValueError if unsatisfiable."""
    if not header:
        return None
    m = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
    if not m:
        return None
    first, last = m.group(1), m.group(2)
    if first == "" and last == "":
        return None
    if first == "":
        n = int(last)
        if n == 0 or size == 0:
            raise ValueError("unsatisfiable")
        start, end = max(0, size - n), size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
        if end >= size:
            end = size - 1
    if start > end or start >= size:
        raise ValueError("unsatisfiable")
    return start, end


def _ascii_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", name or "file")
    return cleaned.strip() or "file"


async def get_thumb(client, m, chat_id: int, msg_id: int):
    key = ("t", chat_id, msg_id)
    hit = thumb_cache.get(key)
    if hit is not None:
        return hit
    mime = "image/jpeg"
    if m.photo is None and not isinstance(getattr(m, "document", None), types.Document):
        wp = webpage_json(m)
        if not (wp and wp.get("thumb")):
            return (None, None)
    doc = getattr(m, "document", None)
    if isinstance(doc, types.Document):
        dm = getattr(doc, "mime_type", "") or ""
        if dm.startswith("image/"):
            mime = dm
    async with dl_semaphore:
        try:
            data = await client.download_media(m, thumb=-1, file=bytes)
        except Exception as e:
            log.warning("thumb download failed (%s/%s): %s", chat_id, msg_id, e)
            return (None, None)
    if not data:
        return (None, None)
    val = (bytes(data), _sniff_image(data) if mime == "image/jpeg" and data[:4] != b"RIFF" else mime)
    if val[1] == "image/jpeg" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        val = (val[0], "image/webp")
    thumb_cache.put(key, val)
    return val


async def get_full_photo(client, m, chat_id: int, msg_id: int):
    key = ("p", chat_id, msg_id)
    hit = photo_cache.get(key)
    if hit is not None:
        return hit
    async with dl_semaphore:
        try:
            data = await client.download_media(m, file=bytes)
        except Exception as e:
            log.warning("photo download failed (%s/%s): %s", chat_id, msg_id, e)
            return (None, None)
    if not data:
        return (None, None)
    val = (bytes(data), _sniff_image(data))
    photo_cache.put(key, val)
    return val


async def iter_file(client, handle, start: int, length: int):
    """Stream `length` bytes of `handle` starting at byte `start` (Range-safe).
    offset is aligned down to 4096 (MTProto requirement) and the skipped bytes
    of the first chunk are dropped."""
    try:
        aligned = start - (start % 4096)
        skip = start - aligned
        remaining = length
        first = True
        stream = client.iter_download(handle, offset=aligned, file_size=length + skip)
        try:
            async for chunk in stream:
                buf = bytes(chunk)
                if first and skip:
                    buf = buf[skip:]
                first = False
                if not buf:
                    continue
                if len(buf) > remaining:
                    buf = buf[:remaining]
                remaining -= len(buf)
                yield buf
                if remaining <= 0:
                    break
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass
    except Exception as e:
        log.warning("media stream aborted: %s", e)


async def stream_media(request: Request, client, m, chat_id: int, dl: bool):
    doc = getattr(m, "document", None)
    photo = getattr(m, "photo", None)
    if not isinstance(doc, types.Document):
        if photo is not None:
            data, mime = await get_full_photo(client, m, chat_id, m.id)
            if data is None:
                raise HTTPException(404, "Could not load photo")
            return Response(data, media_type=mime, headers={
                "Content-Length": str(len(data)), "Accept-Ranges": "bytes",
                "Content-Disposition": 'inline; filename="photo_%s.jpg"' % m.id,
                "Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff"})
        raise HTTPException(404, "This message has no downloadable media")

    size = int(getattr(doc, "size", 0) or 0)
    mime = getattr(doc, "mime_type", None) or "application/octet-stream"
    name = None
    for a in (doc.attributes or []):
        if isinstance(a, types.DocumentAttributeFilename):
            name = a.file_name
    if not name:
        if media_info(m) and media_info(m).get("kind") in ("video", "gif", "videonote"):
            name = "video_%s.mp4" % m.id
        elif media_info(m) and media_info(m).get("kind") == "voice":
            name = "voice_%s.ogg" % m.id
        else:
            name = "file_%s" % m.id
    inline_ok = mime.startswith(("video/", "audio/", "image/")) or mime == "application/pdf"
    disposition = "attachment" if (dl or not inline_ok) else "inline"
    common = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        "Content-Type": mime,
        "Content-Disposition": '%s; filename="%s"; filename*=UTF-8\'\'%s' % (
            disposition, _ascii_name(name), quote(name)),
    }
    if size <= 0:
        async with dl_semaphore:
            try:
                data = await client.download_media(m, file=bytes)
            except Exception as e:
                log.warning("buffered download failed: %s", e)
                data = None
        if not data:
            raise HTTPException(404, "Could not download file")
        headers = dict(common)
        headers["Content-Length"] = str(len(data))
        return Response(data, headers=headers)
    try:
        rng = parse_range_header(request.headers.get("range"), size)
    except ValueError:
        headers = dict(common)
        headers["Content-Range"] = "bytes */%d" % size
        return Response(b"", status_code=416, headers=headers)
    if rng is None:
        headers = dict(common)
        headers["Content-Length"] = str(size)
        return StreamingResponse(iter_file(client, doc, 0, size), status_code=200, headers=headers)
    start, end = rng
    length = end - start + 1
    headers = dict(common)
    headers["Content-Length"] = str(length)
    headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
    return StreamingResponse(iter_file(client, doc, start, length), status_code=206, headers=headers)


@api.get("/media/{chat_id}/{msg_id}")
async def api_media(chat_id: int, msg_id: int, kind: str = "thumb", dl: int = 0, request: Request = None):
    client = await require_client()
    msgs = listify(await client.get_messages(chat_id, ids=[msg_id]))
    m = msgs[0] if msgs else None
    if m is None or isinstance(m, types.MessageEmpty):
        raise HTTPException(404, "Message not found")
    if kind == "thumb":
        data, mime = await get_thumb(client, m, chat_id, msg_id)
        if not data:
            raise HTTPException(404, "No thumbnail")
        return _bytes_response((data, mime), "private, max-age=604800")
    if kind == "photo":
        if getattr(m, "photo", None) is None:
            raise HTTPException(404, "Not a photo")
        data, mime = await get_full_photo(client, m, chat_id, msg_id)
        if not data:
            raise HTTPException(404, "Could not load photo")
        return _bytes_response((data, mime), "private, max-age=604800")
    if kind == "file":
        return await stream_media(request, client, m, chat_id, bool(dl))
    raise HTTPException(400, "Unknown kind")


app.include_router(api)


# ---------------------------------------------------------------------------
# exception handlers — never leak a stack trace to the client
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "Invalid request parameters"}, status_code=422)


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    if isinstance(exc, FloodWaitError):
        secs = int(getattr(exc, "seconds", 0) or 0)
        return JSONResponse({"detail": "Telegram flood limit — retry in %ss" % secs},
                            status_code=429, headers={"Retry-After": str(secs or 1)})
    if isinstance(exc, tg_errors.RPCError):
        log.warning("telegram rpc error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse({"detail": "Telegram error: %s" % exc}, status_code=502)
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)

# ---------------------------------------------------------------------------
# PWA icons (blue square, white paper plane) and manifest
# ---------------------------------------------------------------------------

ICON_192 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAADY0lEQVR42u3cOy5EcRjG4dmTBdiADdiABdiADShUEoWOSjedik4jOolCJxpxG/drjnwnmURhkGBw3uckv17MPPN/nTHTm1g4bqTUen4JAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkD65wBm1s6b/u5tM71y5oFTJoDhtX3w0MytX3oAlQlgeB0OnpqlretmcvHEg6k8AK8v80jRAMwjAWAeCQDzSACYRwLAPBIA5pEAMI8EgHkkAMwjAWAeCQDzSACYRwAAYB4BAIB5BAAA5hEAAJhHAABgHgEAgHkEAADmEQAAmEcAAGAeAQCAeQQAAOYRAACYRwAAYB4BAIB5BAAA5hEAAJhHAABgHgEAgHkEAADmEQAAmEcAAGAeAfCl6hWrXrlWd27a43zv6NEz2DzKATCqqeXT9nSoB7CAbOzftw/oxd2zZ7Z51H0An5lOldPDPIoE4PQwjwBwephHADg9zCMAnB7mEQBOD/MIAKeHeQSA0yN9HgHg9IieRwB0/LSY37xqT4nfOiEA0Fj/X6pmR+3weuKZQAB0vtn+oJ06f+WPZH8E60erJ1dNinqFdRsUADvezAHAjjdzALDjzRwA7HgzBwA7/ptmTr2h5gMx6uSO95FIAOJ2/KirfmYfildnd3z6534BCN3xb131s/tiLAA6u+NT7+YAEL7jzRwAInf8e3dzarZ5kgPQuR1v5gAQuePT37QCIHTHe9MKgLgd700rACJ3vLs5AIx8pe/yjvemFQAfft1HyuVuDgCRAMwcAOIAeNMKgEgA7uYAEAnAzAEgDsDwbo6ZA0AUAHdzAIgE4H9zAIgDYOYAEAnA3RwAIgGYOQDEATBzAIgEYOYAEAnAzAEgDoCZA0AkADMHgEgAZg4AcQDMHEUCMHMUCcDMURwAM0eRAMwcRQIwcxQHwMxRJAAzR5EAzBzFApAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkAAQAH4JAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQBpjL2H+zEzx33ruAAAAAElFTkSuQmCC")
ICON_512 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAALVklEQVR42u3aO05WbRiGUebkAJgAE3ACDIAJMAEKKhMKOqzovo4KOxpiR0JBZ2wMclTkZLZ5d0LCBDDhu9adrPz9jsLl+/wrH7bPJwCgZcVHAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAAAQAD4EAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAALARwAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAABAAAIAAAAAEAAAgAAEAAAAACAAAQALyx9f2r6fDsYf6v7wGAAAgFwMu+Xz9Pmwe30+qnn74NAAKgEgAvu7n/O33+ejet7V74RgAIgEoAvJ7zAAACIBgAzgMACIBwADgPACAAwgHgPACAAIgHgPMAgAAgHADOAwACgHAAOA8ACADiAeA8ACAACAeA8wCAACAcAM4DAAKAeAA4DwAIAMIB4DwAIAAIB4DzAIAAIB4AzgMAAoBwADgPAAgAwgHgPAAgAIgHgPMAgAAgHADOAwACgHAAOA8ACADiAeA8ACAACAeA8wCAACAcAM4DAAKAeAA4DwAIAMIB4DwAIAAIB4DzAIAAEADxAHAeABAAAsCcBwAEgACoz3kAQAAIAOcB5wEAASAAnAf82QEEAALAeQBAACAAnAcABAACwHkAQAAgAJwHAAQAAsB5AEAAIACcBwAEAALAeQBAACAAnAcABAACwHkAQAAgAJwHAAQAAsB5AEAAIACcBwAEAALAeQBAAAgAcx4AEAACwJwHAASAADDnAQABIADMeQAQAAgAcx4ABAACwJwHAAGAADDnAUAAIADMeQAQAAgAcx4ABAACwJwHAAGAADDnAUAAIADMeQAQAAgAcx4ABAACwJwHAAGAADDnAUAAIADMeQAQAAgAcx4ABIAAMHMeAASAADBzHgAEgAAwcx4ABIAAMHMeAAHgIwgAM+cBEAAIADPnARAACAAz5wEQAAgAM+cBEAAIADPnARAACAAz5wEQAAgAM+cBEAAIADPnARAACAAz5wEQAAgAcx5wHgABgAAw5wF/v0EAIADMeQAQAAgAcx4ABIAAEADmPAAIAAFg5jwACAABYOY8AAgAAWDmPAAIAAFg5jwAAgABYOY8AAIAAWDmPAACAAFg5jwAAgABYOY8AAIAAWDmPAACAAFg5jwAAgABYOY8AAIAAWDmPAACAAFg5jwAAgABYOY8AAIAAWDmPAACAAFg5jwAAgABYOY8AAJAAJiZ8wAIgITxQ2Y8QR5/e5x/8JiZ8wAIgPCrwLBz9Hv+l8iIg9MfT34amzkPgAAovxiMOBg/jEYgeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXAzPnAQQAeD0wcx5AAIDXA68H5jwgAACvB14PzHlAAAB4PTDnAQEA4PXAecB5QAAAeD1wHvBnWwAAeD1wHkAAAHg9cB5AAAB4PXAeQAAAeD1wHkAAAHg9cB5AAAD8d+OX08bien4l8EIgAAQAwJKfB8a//Me/+s0JQAAALKmPe5fT1pdf8y8mz/z+J0AB4CMAS3zjH0/Pi5M/8zO0eeZHAADu+OaZXwAAuOObZ34BAOCOb575BQCAO75nfs/8AgDAHd8zPwIAwB3fMz8CAMAd3zM/AgDAHd8zPwIAcMc3z/wIAMAd3zzzIwAAd3zzzI8AANzxzTM/AgBwxzfP/AgAwB3fPPMjAAB3fM/8IAAAd3zP/CAAwB3fHd8zPwgAcMc3z/wIAB8B3PHNMz8CAHDHN8/8CADAHd888yMAAHd888yPAADc8c0zPwIAcMc3z/wIAMAd3zO/Z34EALjjm2d+EADgjm+e+UEAgDu+eeYHAQDu+OaZHwQAuOObZ34QAOCOb575QQCAO7555gcBAO745pkfBADu+O745pkfBADu+Gae+UEA4I5vnvn9PUIAgDu+eeYHAQDu+OaZHwQAuOObZ34QAOCOb575QQCAO7555gcBAO745pkfBADu+H5TmWd+EAC445t55gcBgDu+mWd+EAC445t55gcBgDu+eeYHBADu+OaZHxAAuOObZ35AAOCOb575AQGAO7555gcEgDu+mWd+QAC445t55gcBwHu+6Zt55gcEgAAw88wPCAABYOaZHxAAAsDMMz8IAB9BAJh55gcBgAAw88wPAgABYJ75PfODAEAAmGd+QAAgAMwzPyAAEADmmR8QAAgA88wPCAAEgHnmBwQAAsA88wMCAAFgnvkBAYAAMM/8gABAAJhnfkAAIADMMz8gAASAAPDMDyAABIB55gcQAALAPPMDCAABYJ75AQSAADDP/IAAQACYZ35AACAAzDM/IAAQAOaZHxAACADzzA8IAASAeeYHBAACwDO/Z35AACAAPPMDCAAEgGd+AAGAAPDMDyAAEACe+QEEAALAMz+AAEAAeOYHEAAIAM/8AAJAAJhnfgABIADMMz+AABAA5pkfQAAIAM/8AAIAAeCZH0AAIAA88wMIAASAZ34AAYAA8MwPIAAQAJ75AQQAAsAzP4AAQAB45gcQAAgAz/wAAgAB4JkfQAAgADzzAwgAQgHgmR9AABAJAM/8AAKAUAB45gcQAIQCwDM/gAAgEgCe+QEEAKEA8MwPIAAIBYBnfgABQCQAPPMDCABCAeCZH0AAEAoAz/wAAoBIAHjmBxAAhALAMz+AACAUAJ75AQQAkQDwzA8gAAgFgGd+AAFAKAA88wMIACIB4JkfAAEQCgDP/AAIgFAAeOYHQABEAsAzPwACIBQAnvkBEAChAPDMD4AAAAAEAAAgAABAAPgIACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAACwIcAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAQAD4CAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAACAAAAABAAAIAAAAAEAALxn/wARmx3UM6J8IwAAAABJRU5ErkJggg==")

# ---------------------------------------------------------------------------
# embedded frontend (single page, no build step, no CDN; relative URLs only)
# ---------------------------------------------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<script>
(function(){var p=location.pathname;if(!p.endsWith('/')){p=p.slice(0,p.lastIndexOf('/')+1);}
var b=document.createElement('base');b.href=p;document.head.appendChild(b);})();
</script>
<script>
try{var t=localStorage.getItem('tgw_theme');if(t==='light'||t==='dark'){document.documentElement.setAttribute('data-theme',t);}}catch(e){}
</script>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#3390ec">
<title>TgWeb — Telegram</title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2024%2024'%3E%3Crect%20width='24'%20height='24'%20rx='6'%20fill='%233390ec'/%3E%3Cpath%20d='M3.5%2018.5l17-6.5-17-6.5v5.1l11%201.4-11%201.4z'%20fill='%23fff'/%3E%3C/svg%3E">
<style>
[hidden]{display:none!important}
:root{
  --bg:#f3f4f6;--panel:#ffffff;--hover:#f0f2f5;--active:#e7ebf0;--border:#e4e7eb;
  --fg:#111827;--muted:#6b7280;--accent:#3390ec;--accent-soft:rgba(51,144,236,.12);
  --accent-fg:#ffffff;--danger:#e53935;--badge:#3390ec;--badge-muted:#9aa2ac;
  --chat-bg:#dbe3ec;--chat-dot:#c6d2df;--bub-in:#ffffff;--bub-in-border:#e7e9ee;
  --bub-out:#effdde;--bub-out-fg:#111827;--meta:#9aa2ac;--meta-out:#56a04e;
  --svc:rgba(0,0,0,.28);--link:#2b7fd4;--code-bg:rgba(0,0,0,.06);--shadow:0 4px 24px rgba(0,0,0,.14);
  --scroll:rgba(0,0,0,.22);
}
html[data-theme="dark"]{
  --bg:#181818;--panel:#212121;--hover:#2a2a2a;--active:#303030;--border:#2f2f2f;
  --fg:#f1f1f1;--muted:#9e9e9e;--accent:#4aa3df;--accent-soft:rgba(74,163,223,.16);
  --accent-fg:#ffffff;--danger:#ef5350;--badge:#4aa3df;--badge-muted:#6b6b6b;
  --chat-bg:#141414;--chat-dot:#1e1e1e;--bub-in:#212121;--bub-in-border:#2f2f2f;
  --bub-out:#766ac8;--bub-out-fg:#ffffff;--meta:#8b8b8b;--meta-out:rgba(255,255,255,.65);
  --svc:rgba(0,0,0,.4);--link:#6cb9f0;--code-bg:rgba(255,255,255,.08);--shadow:0 4px 24px rgba(0,0,0,.5);
  --scroll:rgba(255,255,255,.22);
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  overflow:hidden}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;padding:0}
input,textarea{font:inherit;color:inherit}
a{color:var(--link)}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-thumb{background:var(--scroll);border-radius:3px}
.ic{width:20px;height:20px;fill:currentColor;flex:none;display:block}
.ic.big{width:26px;height:26px}
.muted{color:var(--muted)}

/* ---------- auth screens ---------- */
.screen{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:var(--bg);z-index:40}
.auth-card{width:min(400px,92vw);background:var(--panel);border:1px solid var(--border);
  border-radius:16px;padding:28px 24px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:12px}
.auth-card .logo{width:64px;height:64px;border-radius:50%;background:var(--accent);display:grid;place-items:center;margin:0 auto;color:#fff}
.auth-card h1{margin:0;font-size:20px;text-align:center}
.auth-card p{margin:0;font-size:13px;color:var(--muted);text-align:center}
.auth-card input,.auth-card textarea{width:100%;padding:11px 14px;border-radius:10px;
  border:1px solid var(--border);background:var(--bg);outline:none}
.auth-card input:focus,.auth-card textarea:focus{border-color:var(--accent)}
.auth-card textarea{resize:vertical;min-height:90px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.btn{padding:10px 16px;border-radius:10px;background:var(--hover);text-align:center;font-weight:600}
.btn.primary{background:var(--accent);color:var(--accent-fg)}
.btn.danger{background:var(--danger);color:#fff}
.btn:disabled{opacity:.55;cursor:default}
.err{background:rgba(229,57,53,.12);color:var(--danger);border-radius:8px;padding:9px 12px;font-size:13px;white-space:pre-wrap}
.hint{font-size:12px;color:var(--muted);line-height:1.5;text-align:left}
.tabs2{display:flex;gap:6px;background:var(--bg);border-radius:10px;padding:4px}
.tabs2 button{flex:1;padding:8px;border-radius:8px;color:var(--muted);font-weight:600;font-size:14px}
.tabs2 button.on{background:var(--accent);color:var(--accent-fg)}

/* ---------- app layout ---------- */
#scr-app{position:fixed;inset:0;display:flex;background:var(--bg)}
#side{width:100%;max-width:100%;background:var(--panel);display:flex;flex-direction:column;min-width:0;border-right:1px solid var(--border)}
.side-head{display:flex;gap:8px;align-items:center;padding:8px 10px}
.icon-btn{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;color:var(--muted);flex:none;position:relative}
.icon-btn:hover{background:var(--hover)}
.icon-btn.on{color:var(--accent)}
#dlgSearch{flex:1;min-width:0;padding:9px 14px;border-radius:19px;border:0;background:var(--bg);outline:none}
#dlgSearch:focus{box-shadow:0 0 0 2px var(--accent-soft)}
#tabs{display:flex;gap:4px;padding:0 8px 8px;overflow-x:auto;scrollbar-width:none}
#tabs::-webkit-scrollbar{display:none}
#tabs button{padding:6px 13px;border-radius:15px;color:var(--muted);white-space:nowrap;font-size:13px;font-weight:600}
#tabs button.on{background:var(--accent);color:var(--accent-fg)}
.dlg-list{flex:1;overflow-y:auto;padding:0 6px 10px}
.dlg{display:flex;gap:10px;padding:8px 10px;border-radius:12px;cursor:pointer;align-items:center}
.dlg:hover{background:var(--hover)}
.dlg.on{background:var(--accent);color:var(--accent-fg)}
.dlg.on .muted,.dlg.on .dlg-time,.dlg.on .badge{color:var(--accent-fg)}
.dlg.on .badge{background:rgba(255,255,255,.25)}
.ava{width:50px;height:50px;border-radius:50%;flex:none;object-fit:cover;display:grid;place-items:center;
  font-weight:600;font-size:18px;color:#fff;overflow:hidden;background:var(--accent)}
.ava.small{width:40px;height:40px;font-size:15px}
.ava img{width:100%;height:100%;object-fit:cover}
.dlg-body{min-width:0;flex:1}
.dlg-row1{display:flex;gap:6px;align-items:baseline}
.dlg-name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.dlg-time{font-size:12px;color:var(--muted);flex:none}
.dlg-row2{display:flex;gap:6px;align-items:center}
.dlg-last{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:14px;color:var(--muted)}
.badge{background:var(--badge);color:#fff;border-radius:11px;padding:1px 7px;font-size:12px;font-weight:600;flex:none}
.badge.gray{background:var(--badge-muted)}
.dlg .check{width:16px;height:16px;fill:var(--accent)}
.arch-head{display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:12px;cursor:pointer;color:var(--muted);font-weight:600}
.arch-head:hover{background:var(--hover)}
.empty-list{padding:40px 20px;text-align:center;color:var(--muted)}

/* ---------- main / chat ---------- */
#main{flex:1;display:none;flex-direction:column;min-width:0;position:relative;
  background:var(--chat-bg);
  background-image:radial-gradient(var(--chat-dot) 1.2px,transparent 1.2px);
  background-size:22px 22px}
#empty{margin:auto;color:var(--muted);text-align:center}
body.chat-open #side{display:none}
body.chat-open #main{display:flex}
@media(min-width:900px){
  #side{width:390px;max-width:390px}
  #main{display:flex}
  #btnBack{display:none!important}
}
#chatHead{display:flex;gap:10px;align-items:center;padding:7px 10px;background:var(--panel);
  border-bottom:1px solid var(--border);position:relative;z-index:5}
#chatTitleWrap{min-width:0;flex:1;cursor:pointer}
#chatTitle{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#chatSub{font-size:12.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#chatSub.typing{color:var(--accent)}
.chat-actions{display:flex;gap:2px;align-items:center}
#msgs{flex:1;overflow-y:auto;overflow-x:hidden;padding:10px 8px 14px;display:flex;flex-direction:column;gap:2px;scroll-behavior:auto}
.day-chip,.svc{align-self:center;background:var(--svc);color:#fff;border-radius:14px;
  padding:3px 12px;font-size:12.5px;margin:8px 0 4px;max-width:90%;text-align:center;word-break:break-word}
.unread-chip{align-self:center;background:var(--accent);color:#fff;border-radius:14px;padding:3px 14px;
  font-size:12px;font-weight:600;margin:8px 0 4px}
.msg{display:flex;max-width:min(86%,640px);align-self:flex-start;position:relative}
.msg.out{align-self:flex-end;flex-direction:row-reverse}
.msg .ava{width:34px;height:34px;font-size:13px;margin-top:auto;flex:none;cursor:pointer}
.bub{position:relative;background:var(--bub-in);border:1px solid var(--bub-in-border);
  border-radius:14px;padding:5px 9px 6px;min-width:70px;max-width:100%;word-wrap:break-word;
  overflow-wrap:break-word;word-break:break-word}
.msg.out .bub{background:var(--bub-out);border-color:transparent;color:var(--bub-out-fg)}
.msg.first{margin-top:6px}
.msg.tail-in .bub{border-bottom-left-radius:4px}
.msg.tail-out .bub{border-bottom-right-radius:4px}
.msg.flash .bub{animation:flash 1.6s}
@keyframes flash{0%,60%{box-shadow:0 0 0 3px var(--accent)}100%{box-shadow:none}}
.sender{font-weight:600;font-size:13.5px;margin-bottom:1px;cursor:pointer}
.fwd{font-size:13px;color:var(--link);margin-bottom:2px}
.text{white-space:pre-wrap}
.text a{text-decoration:none}
.text a:hover{text-decoration:underline}
.text code,.text pre{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;background:var(--code-bg);border-radius:5px}
.text code{padding:.5px 4px}
.text pre{padding:6px 8px;overflow-x:auto;white-space:pre}
.text blockquote{margin:3px 0;padding:2px 8px;border-left:3px solid var(--link);border-radius:3px}
.spoiler{background:var(--fg);color:transparent;border-radius:3px;cursor:pointer}
html[data-theme="light"] .msg.out .spoiler{background:#2e3b22;color:transparent}
.spoiler.revealed{background:var(--code-bg);color:inherit}
.meta{float:right;margin:8px 0 -2px 9px;font-size:11px;color:var(--meta);display:inline-flex;gap:3px;align-items:center;user-select:none}
.msg.out .meta{color:var(--meta-out)}
.meta .ic{width:15px;height:15px}
.quote{border-left:3px solid var(--accent);background:var(--accent-soft);border-radius:6px;
  padding:2px 8px;margin-bottom:4px;font-size:13.5px;cursor:pointer;overflow:hidden}
.quote b{display:block;color:var(--accent);font-size:13px}
.quote span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:340px;color:var(--muted)}
.msg.out .quote span{color:inherit;opacity:.75}
.photo img,.video-thumb{display:block;max-width:100%;border-radius:10px;cursor:zoom-in;background:var(--code-bg)}
.photo img{width:100%;height:auto}
.photo.wide img{max-height:60vh;width:auto;max-width:100%}
.video-wrap{position:relative;display:inline-block;max-width:100%;cursor:zoom-in}
.video-wrap img{display:block;max-width:100%;max-height:60vh;border-radius:10px;background:var(--code-bg)}
.video-wrap .play{position:absolute;inset:0;margin:auto;width:56px;height:56px;border-radius:50%;
  background:rgba(0,0,0,.5);display:grid;place-items:center;color:#fff}
.video-wrap .dur{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.6);color:#fff;
  border-radius:4px;padding:1px 6px;font-size:12px}
.sticker img{width:160px;height:auto;background:none!important}
.filebox{display:flex;gap:10px;align-items:center;padding:4px 2px;min-width:180px}
.filebox .ficon{width:44px;height:44px;border-radius:50%;background:var(--accent);color:#fff;display:grid;place-items:center;flex:none}
.fname{font-weight:600;font-size:14px;word-break:break-all}
.fsize{font-size:12.5px;color:var(--muted)}
.fdl{font-size:12.5px;color:var(--link);cursor:pointer}
audio{width:min(320px,100%);height:36px}
.pollbox{border:1px solid var(--bub-in-border);border-radius:10px;padding:8px;margin:2px 0;min-width:220px}
.pollbox .q{font-weight:600;margin-bottom:6px}
.pollbox .opt{padding:3px 0;font-size:14px}
.pollbox .opt.chosen{color:var(--accent);font-weight:600}
.pollbox .opt .pc{float:right;color:var(--muted)}
.wp{border:1px solid var(--bub-in-border);border-left:3px solid var(--accent);border-radius:10px;
  padding:6px 10px;margin:3px 0;cursor:pointer;max-width:420px}
.wp .wsite{font-size:12px;color:var(--accent);font-weight:600}
.wp .wtitle{font-weight:600;margin:1px 0}
.wp .wdesc{font-size:13.5px;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.wp img{max-width:100%;border-radius:6px;margin-top:4px}
.reacts{display:flex;gap:4px;margin-top:4px;flex-wrap:wrap}
.react{background:var(--accent-soft);border-radius:12px;padding:1px 9px;font-size:13.5px;cursor:pointer;user-select:none}
.react.mine{background:var(--accent);color:var(--accent-fg)}
.msg .acts{position:absolute;top:-14px;right:6px;display:flex;gap:2px;opacity:0;transition:opacity .12s}
.msg:hover .acts,.msg.menu-open .acts{opacity:1}
.msg.out .acts{right:auto;left:6px}
.acts .abtn{width:28px;height:28px;border-radius:50%;background:var(--panel);border:1px solid var(--border);
  display:grid;place-items:center;color:var(--muted);box-shadow:0 2px 8px rgba(0,0,0,.15)}
.acts .abtn:hover{color:var(--accent)}
.acts .abtn .ic{width:16px;height:16px}
.selcheck{position:absolute;top:4px;left:-30px;width:22px;height:22px;border-radius:50%;
  border:2px solid var(--muted);background:var(--panel);display:none}
.msg.selecting{cursor:pointer}
.msg.selecting .selcheck{display:block}
.msg.selected .selcheck{background:var(--accent);border-color:var(--accent);display:grid;place-items:center;color:#fff}
.msg.selected .bub{box-shadow:0 0 0 2px var(--accent)}
.msg.selected .selcheck::after{content:"✓";font-size:14px}
#scrollDown{position:absolute;right:16px;bottom:84px;width:46px;height:46px;border-radius:50%;
  background:var(--panel);border:1px solid var(--border);box-shadow:var(--shadow);
  display:grid;place-items:center;color:var(--accent);z-index:6}
#scrollDown .mini{position:absolute;top:-6px;right:-4px;background:var(--badge);color:#fff;
  border-radius:10px;padding:0 6px;font-size:12px;font-weight:600}
#selBar{display:flex;gap:6px;align-items:center;background:var(--panel);border-top:1px solid var(--border);padding:8px 10px}
#selBar b{margin-right:auto}

/* ---------- composer ---------- */
#replyBar,#attachBar,#editBar{display:flex;gap:10px;align-items:center;background:var(--panel);
  border-top:1px solid var(--border);padding:6px 12px;font-size:13.5px}
#replyBar .rb,#editBar .rb{border-left:3px solid var(--accent);padding-left:8px;min-width:0;flex:1}
#replyBar b,#editBar b{color:var(--accent);display:block;font-size:13px}
#replyBar span,#editBar span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}
#composer{display:flex;gap:8px;align-items:flex-end;background:var(--panel);border-top:1px solid var(--border);padding:8px 10px;padding-bottom:max(8px,env(safe-area-inset-bottom))}
#input{flex:1;min-width:0;resize:none;border:0;outline:none;background:var(--bg);border-radius:18px;
  padding:10px 14px;max-height:132px;line-height:1.4}
#btnSend{width:42px;height:42px;border-radius:50%;background:var(--accent);color:var(--accent-fg);
  display:grid;place-items:center;flex:none}
#btnSend:disabled{opacity:.5}
#btnAttach{color:var(--muted)}

/* ---------- menus / modals / misc ---------- */
.menu{position:fixed;background:var(--panel);border:1px solid var(--border);border-radius:12px;
  box-shadow:var(--shadow);padding:6px;z-index:70;min-width:190px;max-height:70vh;overflow-y:auto}
.menu button{display:flex;gap:10px;align-items:center;width:100%;padding:9px 12px;border-radius:8px;text-align:left;font-size:14px}
.menu button:hover{background:var(--hover)}
.menu button.danger{color:var(--danger)}
.menu .sep{height:1px;background:var(--border);margin:5px 8px}
.menu .rx{display:flex;gap:2px;padding:6px;flex-wrap:wrap}
.menu .rx button{font-size:22px;padding:4px 7px}
#modal{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:90;display:flex;align-items:center;justify-content:center;padding:14px}
#modalBox{background:var(--panel);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);
  width:min(560px,100%);max-height:86vh;display:flex;flex-direction:column;overflow:hidden}
#modalBox.wide{width:min(760px,100%)}
.mhead{display:flex;gap:10px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--border)}
.mhead b{flex:1;font-size:16px}
.mhead input{flex:1;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);outline:none}
.mbody{overflow-y:auto;padding:8px}
.mtabs{display:flex;gap:4px;padding:8px 8px 0}
.mtabs button{padding:6px 12px;border-radius:14px;color:var(--muted);font-size:13px;font-weight:600}
.mtabs button.on{background:var(--accent);color:var(--accent-fg)}
.gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:4px}
.gal-item{position:relative;padding-top:100%;overflow:hidden;border-radius:8px;cursor:pointer;background:var(--code-bg)}
.gal-item img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.gal-item .vdur{position:absolute;right:4px;bottom:4px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;padding:0 5px;border-radius:4px}
.gal-item .gcheck{position:absolute;left:4px;top:4px}
.rowitem{display:flex;gap:10px;align-items:center;padding:7px 8px;border-radius:10px;cursor:pointer}
.rowitem:hover{background:var(--hover)}
.rowitem .ri-body{min-width:0;flex:1}
.rowitem .ri-t{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rowitem .ri-s{font-size:12.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rowitem .tag{font-size:11px;color:var(--accent);border:1px solid var(--accent);border-radius:8px;padding:0 6px;margin-left:6px}
#viewer{position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:120;display:flex;flex-direction:column}
.vtop{display:flex;gap:6px;align-items:center;padding:10px 14px;color:#fff}
.vtop b{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:14px}
.vtop .icon-btn{color:#fff}
.vtop .icon-btn:hover{background:rgba(255,255,255,.15)}
.vbody{flex:1;display:flex;align-items:center;justify-content:center;min-height:0;padding:0 8px 16px}
.vbody img{max-width:100%;max-height:100%;border-radius:4px}
.vbody video{max-width:100%;max-height:100%;border-radius:4px}
#toasts{position:fixed;left:50%;transform:translateX(-50%);bottom:24px;z-index:200;display:flex;
  flex-direction:column;gap:8px;align-items:center;pointer-events:none}
.toast{background:var(--panel);color:var(--fg);border:1px solid var(--border);box-shadow:var(--shadow);
  border-radius:10px;padding:10px 18px;font-size:14px;max-width:88vw;animation:tin .18s}
.toast.err{border-color:var(--danger);color:var(--danger)}
@keyframes tin{from{opacity:0;transform:translateY(8px)}}
.spinner{width:26px;height:26px;border:3px solid var(--accent-soft);border-top-color:var(--accent);
  border-radius:50%;animation:spin .8s linear infinite;margin:14px auto}
@keyframes spin{to{transform:rotate(360deg)}}
.search-hit{border-radius:10px;padding:8px 10px;cursor:pointer}
.search-hit:hover{background:var(--hover)}
.search-hit .sh-t{font-size:13px;color:var(--accent);font-weight:600}
.search-hit .sh-b{font-size:14px;word-break:break-word;max-height:60px;overflow:hidden}
.search-hit .sh-d{font-size:12px;color:var(--muted)}
</style>
</head>
<body>
<svg style="display:none" xmlns="http://www.w3.org/2000/svg">
<symbol id="i-send" viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2v7z"/></symbol>
<symbol id="i-back" viewBox="0 0 24 24"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></symbol>
<symbol id="i-close" viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></symbol>
<symbol id="i-menu" viewBox="0 0 24 24"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></symbol>
<symbol id="i-more" viewBox="0 0 24 24"><path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></symbol>
<symbol id="i-attach" viewBox="0 0 24 24"><path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/></symbol>
<symbol id="i-dl" viewBox="0 0 24 24"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></symbol>
<symbol id="i-down" viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></symbol>
<symbol id="i-reply" viewBox="0 0 24 24"><path d="M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z"/></symbol>
<symbol id="i-forward" viewBox="0 0 24 24"><path d="M14 9V5l7 7-7 7v-4.1c-5 0-8.5 1.6-11 5.1 1-5 4-10 11-11z"/></symbol>
<symbol id="i-edit" viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></symbol>
<symbol id="i-trash" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></symbol>
<symbol id="i-pin" viewBox="0 0 24 24"><path d="M14 4v5c0 1.12.37 2.16 1 3H9c.65-.86 1-1.9 1-3V4h4m3-2H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3V4h1c.55 0 1-.45 1-1s-.45-1-1-1z"/></symbol>
<symbol id="i-bell" viewBox="0 0 24 24"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></symbol>
<symbol id="i-bell-off" viewBox="0 0 24 24"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-1.85-.59-3.51-1.6-4.83L18 4.5 16.5 3 3 17.5 4.5 19l1.67-1.67c.4.7 1.83 1.67 1.83 1.67v1h8v-1s1.43-.97 1.83-1.67l.3.3V16zm-2 0H8v-5c0-.71.13-1.36.35-1.96L16.83 16h-.83z"/></symbol>
<symbol id="i-users" viewBox="0 0 24 24"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></symbol>
<symbol id="i-image" viewBox="0 0 24 24"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></symbol>
<symbol id="i-play" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></symbol>
<symbol id="i-file" viewBox="0 0 24 24"><path d="M6 2c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6H6zm7 7V3.5L18.5 9H13z"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></symbol>
<symbol id="i-checks" viewBox="0 0 24 24"><path d="M18 7l-1.41-1.41-6.34 6.34 1.41 1.41L18 7zm4.24-1.41L11.66 16.17 7.5 12.01l-1.41 1.41 5.57 5.58 9.99-9.99-1.41-1.42zM.41 13.41L6 19l1.41-1.42L1.83 12 .41 13.41z"/></symbol>
<symbol id="i-mic" viewBox="0 0 24 24"><path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/></symbol>
<symbol id="i-music" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></symbol>
<symbol id="i-archive" viewBox="0 0 24 24"><path d="M20.54 5.23l-1.39-1.68C18.88 3.21 18.47 3 18 3H6c-.47 0-.88.21-1.16.55L3.46 5.23C3.17 5.57 3 6.02 3 6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.48-.17-.93-.46-1.27zM12 17.5L6.5 12H10v-2h4v2h3.5L12 17.5zM5.12 5l.81-1h12l.94 1H5.12z"/></symbol>
<symbol id="i-unarchive" viewBox="0 0 24 24"><path d="M20.55 5.22l-1.39-1.68C18.88 3.21 18.47 3 18 3H6c-.47 0-.88.21-1.15.55L3.46 5.22C3.17 5.57 3 6.01 3 6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.49-.17-.93-.45-1.28zM12 9.5l5.5 5.5H14v2h-4v-2H6.5L12 9.5zM5.12 5l.82-1h12l.93 1H5.12z"/></symbol>
<symbol id="i-moon" viewBox="0 0 24 24"><path d="M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-.98 1.37-2.58 2.26-4.4 2.26-2.98 0-5.4-2.42-5.4-5.4 0-1.81.89-3.42 2.26-4.4-.44-.06-.9-.1-1.36-.1z"/></symbol>
<symbol id="i-sun" viewBox="0 0 24 24"><path d="M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zm0-5l2.39 3.42h-4.78L12 2zm0 20l-2.39-3.42h4.78L12 22zM2 12l3.39-2.39v4.78L2 12zm20 0l-3.39 2.39V9.61L22 12zM4.93 4.93l4.02.69-2.36 2.36-1.66-3.05zm14.14 14.14l-4.02-.69 2.36-2.36 1.66 3.05zM19.07 4.93l-.69 4.02-2.36-2.36 3.05-1.66zM4.93 19.07l.69-4.02 2.36 2.36-3.05 1.66z"/></symbol>
<symbol id="i-logout" viewBox="0 0 24 24"><path d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.59L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/></symbol>
<symbol id="i-lock" viewBox="0 0 24 24"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></symbol>
<symbol id="i-copy" viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></symbol>
<symbol id="i-select" viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></symbol>
<symbol id="i-saved" viewBox="0 0 24 24"><path d="M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2z"/></symbol>
</svg>

<div id="toasts"></div>

<!-- ======== site password screen ======== -->
<section id="scr-site" class="screen" hidden>
  <form id="siteForm" class="auth-card">
    <div class="logo"><svg class="ic big"><use href="#i-send"/></svg></div>
    <h1>TgWeb</h1>
    <p>This site is password-protected.</p>
    <input id="sitePass" type="password" placeholder="Site password" autocomplete="current-password" required>
    <button class="btn primary" type="submit" id="siteBtn">Unlock</button>
    <div class="err" id="siteErr" hidden></div>
  </form>
</section>

<!-- ======== Telegram login screen ======== -->
<section id="scr-tg" class="screen" hidden>
  <div class="auth-card">
    <div class="logo"><svg class="ic big"><use href="#i-send"/></svg></div>
    <h1>Telegram login</h1>
    <div class="tabs2">
      <button id="tabPhone" class="on" type="button">Phone number</button>
      <button id="tabSession" type="button">Session string</button>
    </div>
    <div id="panePhone">
      <input id="tgPhone" type="tel" placeholder="+8801XXXXXXXXX" autocomplete="tel">
      <button class="btn primary" id="tgSendCode" type="button">Send login code</button>
      <div id="tgCodeRow" hidden>
        <input id="tgCode" inputmode="numeric" autocomplete="one-time-code" placeholder="Code from the Telegram app">
        <button class="btn primary" id="tgVerifyCode" type="button">Verify code</button>
      </div>
      <div id="tg2faRow" hidden>
        <input id="tg2fa" type="password" autocomplete="current-password" placeholder="Two-factor password">
        <button class="btn primary" id="tgVerifyPass" type="button">Verify password</button>
      </div>
      <p class="hint">Enter the phone number with country code. Telegram will send a login code to your other device (app/SMS).</p>
    </div>
    <div id="paneSession" hidden>
      <textarea id="tgSession" placeholder="Paste your Telethon StringSession here" spellcheck="false"></textarea>
      <button class="btn primary" id="tgImport" type="button">Import session</button>
      <p class="hint">To get a session string, log in once with Telethon on your computer and print <span style="font-family:ui-monospace,monospace">StringSession.save()</span>. It grants full access to your account — never share it.</p>
    </div>
    <div class="err" id="tgErr" hidden></div>
  </div>
</section>

<!-- ======== main app ======== -->
<section id="scr-app" class="screen" hidden>
  <aside id="side">
    <header class="side-head">
      <button id="btnMenu" class="icon-btn" title="Menu"><svg class="ic"><use href="#i-menu"/></svg></button>
      <input id="dlgSearch" type="search" placeholder="Search chats" autocomplete="off">
    </header>
    <nav id="tabs">
      <button data-tab="all" class="on">All</button>
      <button data-tab="user">Chats</button>
      <button data-tab="group">Groups</button>
      <button data-tab="channel">Channels</button>
      <button data-tab="bot">Bots</button>
      <button data-tab="saved">Saved</button>
    </nav>
    <div id="dlgList" class="dlg-list"></div>
  </aside>
  <main id="main">
    <div id="empty"><svg class="ic big" style="margin:0 auto 10px;opacity:.4"><use href="#i-send"/></svg><div>Select a chat to start messaging</div></div>
    <div id="chat" hidden style="display:flex;flex-direction:column;height:100%;min-height:0">
      <header id="chatHead">
        <button id="btnBack" class="icon-btn" title="Back"><svg class="ic"><use href="#i-back"/></svg></button>
        <div id="chatAvaWrap"></div>
        <div id="chatTitleWrap">
          <div id="chatTitle"></div>
          <div id="chatSub"></div>
        </div>
        <div class="chat-actions">
          <button id="btnMarkRead" class="icon-btn" title="Mark as read"><svg class="ic"><use href="#i-checks"/></svg></button>
          <button id="btnCSearch" class="icon-btn" title="Search in chat"><svg class="ic"><use href="#i-search"/></svg></button>
          <button id="btnGallery" class="icon-btn" title="Media gallery"><svg class="ic"><use href="#i-image"/></svg></button>
          <button id="btnMembers" class="icon-btn" title="Members"><svg class="ic"><use href="#i-users"/></svg></button>
          <button id="btnChatMenu" class="icon-btn" title="More"><svg class="ic"><use href="#i-more"/></svg></button>
        </div>
      </header>
      <div id="csearchBar" hidden style="background:var(--panel);border-bottom:1px solid var(--border)">
        <div style="display:flex;gap:8px;padding:8px 10px;align-items:center">
          <input id="csearchInput" placeholder="Search in this chat" style="flex:1;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);outline:none">
          <button id="csearchClose" class="icon-btn"><svg class="ic"><use href="#i-close"/></svg></button>
        </div>
        <div id="csearchResults" style="max-height:40vh;overflow-y:auto;padding:0 8px 8px"></div>
      </div>
      <div id="msgs"></div>
      <button id="scrollDown" hidden><svg class="ic"><use href="#i-down"/></svg><span class="mini" id="sdBadge" hidden></span></button>
      <div id="selBar" hidden>
        <b id="selCount">0 selected</b>
        <button id="selForward" class="btn"><svg class="ic" style="display:inline-block;vertical-align:-4px"><use href="#i-forward"/></svg> Forward</button>
        <button id="selDelete" class="btn danger"><svg class="ic" style="display:inline-block;vertical-align:-4px"><use href="#i-trash"/></svg> Delete</button>
        <button id="selCancel" class="icon-btn"><svg class="ic"><use href="#i-close"/></svg></button>
      </div>
      <div id="editBar" hidden>
        <div class="rb"><b>Edit message</b><span id="editSnip"></span></div>
        <button id="editCancel" class="icon-btn"><svg class="ic"><use href="#i-close"/></svg></button>
      </div>
      <div id="replyBar" hidden>
        <div class="rb"><b id="replyTitle"></b><span id="replySnip"></span></div>
        <button id="replyCancel" class="icon-btn"><svg class="ic"><use href="#i-close"/></svg></button>
      </div>
      <div id="attachBar" hidden>
        <span id="attachIcon">📎</span><span id="attachName"></span>
        <button id="attachCancel" class="icon-btn"><svg class="ic"><use href="#i-close"/></svg></button>
      </div>
      <footer id="composer">
        <button id="btnAttach" class="icon-btn" title="Attach file"><svg class="ic"><use href="#i-attach"/></svg></button>
        <textarea id="input" rows="1" placeholder="Message" autocomplete="off"></textarea>
        <button id="btnSend" title="Send"><svg class="ic"><use href="#i-send"/></svg></button>
      </footer>
      <input id="fileInput" type="file" hidden>
    </div>
  </main>
</section>

<!-- generic modal + fullscreen media viewer -->
<div id="modal" hidden><div id="modalBox"></div></div>
<div id="viewer" hidden></div>
<script>
(function(){
'use strict';
/* ============================== helpers ============================== */
var $ = function(s, r){ return (r||document).querySelector(s); };
var $$ = function(s, r){ return Array.prototype.slice.call((r||document).querySelectorAll(s)); };
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
var COLORS = ['#e17076','#7bc862','#e5ca77','#65aadd','#a695e7','#ee7aae','#faa774'];
function colorFor(id){ return COLORS[Math.abs(id||0)%COLORS.length]; }
function initials(name){
  var p = String(name||'?').trim().split(/\s+/);
  var s = (p[0]?p[0][0]:'?') + (p[1]?p[1][0]:'');
  return s.toUpperCase() || '?';
}
function fmtSize(n){
  n = Number(n)||0;
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n/1024).toFixed(1) + ' KB';
  if (n < 1073741824) return (n/1048576).toFixed(1) + ' MB';
  return (n/1073741824).toFixed(2) + ' GB';
}
function fmtDur(s){
  s = Math.max(0, Math.round(Number(s)||0));
  var m = Math.floor(s/60); s = s%60;
  return m + ':' + (s<10?'0':'') + s;
}
function fmtClock(iso){
  if (!iso) return '';
  var d = new Date(iso);
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}
function sameDay(a, b){
  return a.getFullYear()===b.getFullYear() && a.getMonth()===b.getMonth() && a.getDate()===b.getDate();
}
function dayLabel(d){
  var now = new Date();
  var yd = new Date(now.getTime() - 86400000);
  if (sameDay(d, now)) return 'Today';
  if (sameDay(d, yd)) return 'Yesterday';
  return d.toLocaleDateString([], {day:'numeric', month:'long', year: d.getFullYear()!==now.getFullYear()?'numeric':undefined});
}
function fmtListTime(iso){
  if (!iso) return '';
  var d = new Date(iso), now = new Date();
  if (sameDay(d, now)) return fmtClock(iso);
  var diff = (now - d) / 86400000;
  if (diff < 6) return d.toLocaleDateString([], {weekday:'short'});
  return d.toLocaleDateString([], {day:'2-digit', month:'2-digit', year: d.getFullYear()!==now.getFullYear()?'2-digit':undefined});
}
function avatarHTML(id, name, hasPhoto, cls, extraBg){
  var bg = extraBg || colorFor(id);
  var core = esc(initials(name));
  if (hasPhoto){
    return '<div class="ava '+(cls||'')+'" style="background:'+bg+'">'+core+
      '<img src="api/avatar/'+Number(id)+'" loading="lazy" alt="" onerror="this.remove()"></div>';
  }
  return '<div class="ava '+(cls||'')+'" style="background:'+bg+'">'+core+'</div>';
}
function toast(msg, kind){
  if (!msg) return;
  var box = $('#toasts');
  var t = document.createElement('div');
  t.className = 'toast' + (kind==='err' ? ' err' : '');
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(function(){ t.style.opacity='0'; t.style.transition='opacity .3s'; }, 2800);
  setTimeout(function(){ t.remove(); }, 3200);
}
var lastPollToast = 0;
function toastErr(e){
  if (e && e.status === 401) return;             // already routed to site login
  if (e && e.status === 409) return;             // already routed to tg login
  var msg = (e && e.detail) ? e.detail : 'Something went wrong';
  var now = Date.now();
  if (now - lastPollToast > 8000){ lastPollToast = now; toast(msg, 'err'); }
}

/* ============================== state ============================== */
var S = {
  me: null,
  tab: 'all',
  dialogs: [],
  dialogById: {},
  dlg: null, current: null,
  msgs: [], msgById: {},
  oldest: 0, newest: 0, hasMore: true, loadingOlder: false,
  readIn: 0, readOut: 0,
  replyTo: null, editing: null, pendingFile: null,
  selecting: false, selection: {},
  typingSent: 0, dlgTimer: null, pollTimer: null,
  archivedOpen: false, viewerOpen: false, csearchOpen: false,
  galleryTab: 'media', galleryOffset: 0, galleryDone: false,
  lastPollOk: true
};

/* ============================== api ============================== */
function api(path, opts){
  opts = opts || {};
  var init = { method: opts.method || 'GET', credentials: 'same-origin', headers: {} };
  if (opts.form){ init.body = opts.form; }
  else if (opts.body !== undefined){
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  return fetch(path, init).then(function(res){
    if (res.status === 401){ showScreen('site'); throw {status:401, detail:'Locked'}; }
    return res.json().catch(function(){ return null; }).then(function(data){
      if (!res.ok){
        var detail = (data && data.detail) ? data.detail : ('HTTP ' + res.status);
        if (res.status === 409 && /not logged in/i.test(String(detail))) showScreen('tg');
        throw {status: res.status, detail: detail};
      }
      return data;
    });
  }).catch(function(e){
    if (e && (e.status !== undefined)) throw e;
    throw {status: 0, detail: 'Network error — check your connection'};
  });
}

/* ============================== screens ============================== */
function showScreen(name){
  $('#scr-site').hidden = name !== 'site';
  $('#scr-tg').hidden = name !== 'tg';
  $('#scr-app').hidden = name !== 'app';
  if (name === 'site') setTimeout(function(){ $('#sitePass').focus(); }, 50);
}
function showTgLogin(errText){
  showScreen('tg');
  var box = $('#tgErr');
  if (errText){ box.textContent = errText; box.hidden = false; } else { box.hidden = true; }
}
function tgErr(msg){
  var box = $('#tgErr');
  if (msg){ box.textContent = msg; box.hidden = false; } else { box.hidden = true; }
}
function busy(btn, on, label){
  if (on){ btn.dataset.old = btn.textContent; btn.textContent = label || 'Please wait…'; btn.disabled = true; }
  else { btn.textContent = btn.dataset.old || btn.textContent; btn.disabled = false; }
}

/* ============================== boot ============================== */
function boot(){
  return api('api/status').then(function(r){
    if (r.tg) enterApp(r.me);
    else showTgLogin(r.error);
  }).catch(function(){ /* 401 already showed site screen */ });
}
function enterApp(me){
  S.me = me || S.me;
  showScreen('app');
  loadDialogs(false);
  if (S.dlgTimer) clearInterval(S.dlgTimer);
  S.dlgTimer = setInterval(function(){
    if (!document.hidden) loadDialogs(true);
  }, 30000);
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden){
      if (S.current != null) pollMessages();
      loadDialogs(true);
    }
  });
}

/* ============================== site login ============================== */
$('#siteForm').addEventListener('submit', function(e){
  e.preventDefault();
  var btn = $('#siteBtn');
  busy(btn, true, 'Checking…');
  api('api/login', {method:'POST', body:{password: $('#sitePass').value}})
    .then(function(){ $('#sitePass').value=''; $('#siteErr').hidden = true; boot(); })
    .catch(function(err){
      $('#siteErr').textContent = err.detail || 'Login failed';
      $('#siteErr').hidden = false;
    })
    .finally(function(){ busy(btn, false); });
});

/* ============================== telegram login ============================== */
$('#tabPhone').addEventListener('click', function(){
  $('#tabPhone').classList.add('on'); $('#tabSession').classList.remove('on');
  $('#panePhone').hidden = false; $('#paneSession').hidden = true; tgErr(null);
});
$('#tabSession').addEventListener('click', function(){
  $('#tabSession').classList.add('on'); $('#tabPhone').classList.remove('on');
  $('#paneSession').hidden = false; $('#panePhone').hidden = true; tgErr(null);
});
$('#tgSendCode').addEventListener('click', function(){
  var phone = $('#tgPhone').value.trim();
  if (!phone){ tgErr('Enter your phone number with country code.'); return; }
  var btn = this; busy(btn, true, 'Sending…');
  api('api/tg/login_phone', {method:'POST', body:{phone: phone}})
    .then(function(){
      tgErr(null);
      $('#tgCodeRow').hidden = false;
      $('#tgCode').focus();
    })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
});
$('#tgVerifyCode').addEventListener('click', function(){
  var code = $('#tgCode').value.trim();
  if (!code){ tgErr('Enter the login code.'); return; }
  var btn = this; busy(btn, true, 'Checking…');
  api('api/tg/login_code', {method:'POST', body:{code: code}})
    .then(function(r){
      if (r.need_password){
        tgErr(null);
        $('#tg2faRow').hidden = false;
        $('#tg2fa').focus();
      } else { enterApp(r.me); }
    })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
});
$('#tgVerifyPass').addEventListener('click', function(){
  var btn = this; busy(btn, true, 'Checking…');
  api('api/tg/login_password', {method:'POST', body:{password: $('#tg2fa').value}})
    .then(function(r){ enterApp(r.me); })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
});
$('#tgImport').addEventListener('click', function(){
  var s = $('#tgSession').value.trim();
  if (!s){ tgErr('Paste the session string first.'); return; }
  var btn = this; busy(btn, true, 'Validating…');
  api('api/tg/import_session', {method:'POST', body:{session: s}})
    .then(function(r){ $('#tgSession').value=''; enterApp(r.me); })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
});

/* ============================== dialogs ============================== */
function loadDialogs(silent){
  return api('api/dialogs').then(function(r){
    S.dialogs = r.dialogs || [];
    S.dialogById = {};
    S.dialogs.forEach(function(d){ S.dialogById[d.id] = d; });
    if (r.me) S.me = r.me;
    if (S.current != null && !S.dialogById[S.current]) closeChat();
    renderDialogs();
  }).catch(function(e){
    if (!silent) toastErr(e);
    if (e.status === 409) showTgLogin(e.detail);
  });
}
function tabFilter(d){
  if (S.tab === 'saved') return d.is_self;
  if (d.archived) return false;
  switch (S.tab){
    case 'user': return d.type === 'user' && !d.is_bot && !d.is_self;
    case 'group': return d.type === 'group';
    case 'channel': return d.type === 'channel';
    case 'bot': return d.is_bot;
    default: return true;
  }
}
function previewOf(m){
  if (!m) return '';
  if (m.service) return m.html || 'Service message';
  if (m.raw) return m.raw;
  if (m.media) return m.media.label || 'Media';
  if (m.webpage) return '🔗 Link';
  return '';
}
function dialogRow(d){
  var last = d.last || {};
  var pre = '';
  if (last.out) pre = 'You: ';
  else if (last.sender && (d.type === 'group') && last.snippet) pre = last.sender + ': ';
  var snip = last.snippet ? esc(previewOfText(last.snippet)) : '&nbsp;';
  var badge = '';
  if (d.unread > 0) badge = '<span class="badge'+(d.muted?' gray':'')+'">'+(d.unread>999?'999+':d.unread)+'</span>';
  else if (d.mentions > 0) badge = '<span class="badge">@</span>';
  var nameExtras = (d.pinned ? '📌 ' : '') + (d.muted ? '🔇 ' : '') + (d.verified ? '✔ ' : '');
  return '<div class="dlg'+(S.current===d.id?' on':'')+'" data-id="'+d.id+'">'+
    avatarHTML(d.id, d.is_self ? 'Saved' : d.name, d.has_photo)+
    '<div class="dlg-body">'+
      '<div class="dlg-row1"><span class="dlg-name">'+nameExtras+esc(d.name)+'</span>'+
      '<span class="dlg-time">'+fmtListTime(last.date)+'</span></div>'+
      '<div class="dlg-row2"><span class="dlg-last">'+pre+snip+'</span>'+badge+'</div>'+
    '</div></div>';
}
function previewOfText(s){ return s; }
function renderDialogs(){
  var box = $('#dlgList');
  var q = $('#dlgSearch').value.trim().toLowerCase();
  var shown = S.dialogs.filter(function(d){
    if (!tabFilter(d)) return false;
    if (q && d.name.toLowerCase().indexOf(q) < 0) return false;
    return true;
  });
  var archived = (S.tab === 'all' && !q) ? S.dialogs.filter(function(d){ return d.archived; }) : [];
  var html = '';
  if (archived.length){
    html += '<div class="arch-head" data-arch="1"><svg class="ic"><use href="#i-archive"/></svg> Archived chats <span style="margin-left:auto">'+archived.length+'</span></div>';
    if (S.archivedOpen) archived.forEach(function(d){ html += dialogRow(d); });
  }
  html += shown.map(dialogRow).join('');
  if (!html) html = '<div class="empty-list">No chats here</div>';
  box.innerHTML = html;
}
$('#tabs').addEventListener('click', function(e){
  var b = e.target.closest('button'); if (!b) return;
  S.tab = b.dataset.tab;
  $$('#tabs button').forEach(function(x){ x.classList.toggle('on', x === b); });
  renderDialogs();
});
$('#dlgSearch').addEventListener('input', renderDialogs);
$('#dlgList').addEventListener('click', function(e){
  var arch = e.target.closest('[data-arch]');
  if (arch){ S.archivedOpen = !S.archivedOpen; renderDialogs(); return; }
  var row = e.target.closest('.dlg'); if (!row) return;
  openChat(Number(row.dataset.id));
});

/* ============================== chat view ============================== */
function closeChat(){
  S.current = null; S.dlg = null;
  S.msgs = []; S.msgById = {};
  S.oldest = 0; S.newest = 0; S.hasMore = true;
  clearReply(); clearEditing(); clearPendingFile(); exitSelecting(); closeCSearch();
  stopPolling();
  document.body.classList.remove('chat-open');
  $('#chat').hidden = true;
  $('#empty').hidden = false;
  renderDialogs();
}
function updateChatHeader(){
  var d = S.dlg; if (!d) return;
  $('#chatAvaWrap').innerHTML = avatarHTML(d.id, d.is_self ? 'Saved' : d.name, d.has_photo, 'small');
  $('#chatTitle').textContent = (d.verified ? '✔ ' : '') + d.name;
  setSubDefault();
}
function setSubDefault(){
  var d = S.dlg; if (!d) return;
  var sub = '';
  if (d.is_bot) sub = 'bot';
  else if (d.type === 'user') sub = d.status || (d.username ? '@' + d.username : '');
  else if (d.members) sub = Number(d.members).toLocaleString() + (d.type === 'channel' ? ' subscribers' : ' members');
  else if (d.type === 'group') sub = 'group';
  else sub = d.username ? '@' + d.username : 'channel';
  var el = $('#chatSub');
  el.textContent = sub || ' ';
  el.classList.remove('typing');
}
function setTyping(names){
  var el = $('#chatSub');
  if (names && names.length && S.dlg){
    el.textContent = names.length > 1 ? names.length + ' people are typing…' : names[0] + ' is typing…';
    el.classList.add('typing');
  } else { setSubDefault(); }
}
function openChat(id){
  var d = S.dialogById[id];
  if (!d){ toast('Chat not found — refreshing'); loadDialogs(true); return; }
  if (S.current === id){ document.body.classList.add('chat-open'); return; }
  S.current = id; S.dlg = d;
  S.msgs = []; S.msgById = {};
  S.oldest = 0; S.newest = 0; S.hasMore = true;
  S.readIn = 0; S.readOut = 0;
  clearReply(); clearEditing(); clearPendingFile(); exitSelecting(); closeCSearch();
  document.body.classList.add('chat-open');
  $('#empty').hidden = true;
  $('#chat').hidden = false;
  $('#msgs').innerHTML = '<div class="spinner"></div>';
  updateChatHeader();
  renderDialogs();
  startPolling();
  var chatId = id;
  api('api/messages?chat_id=' + id + '&limit=50').then(function(r){
    if (S.current !== chatId) return;
    S.readIn = r.read_inbox_max_id || 0;
    S.readOut = r.read_outbox_max_id || 0;
    var list = (r.messages || []).slice().reverse();
    S.msgs = list;
    S.msgById = {};
    list.forEach(function(m){ S.msgById[m.id] = m; });
    S.oldest = list.length ? list[0].id : 0;
    S.newest = list.length ? list[list.length - 1].id : 0;
    S.hasMore = list.length >= 50;
    renderAllMessages();
    scrollBottom();
    setTyping(r.typing || []);
    updateFab();
    if (window.matchMedia('(min-width:900px)').matches) $('#input').focus();
  }).catch(function(e){
    $('#msgs').innerHTML = '';
    toastErr(e);
  });
}
$('#btnBack').addEventListener('click', closeChat);
$('#chatTitleWrap').addEventListener('click', function(){
  if (S.dlg && (S.dlg.type === 'group' || S.dlg.type === 'channel')) openMembers();
});

/* ============================== message rendering ============================== */
function sameMinute(a, b){
  var x = a && a.date ? new Date(a.date) : null;
  var y = b && b.date ? new Date(b.date) : null;
  return !!(x && y && Math.abs(x - y) < 10 * 60 * 1000);
}
function isFirstOfGroup(m, prev){
  if (!prev) return true;
  if (prev.service || m.service) return true;
  var pid = prev.sender ? prev.sender.id : null;
  var mid = m.sender ? m.sender.id : null;
  return prev.out !== m.out || pid !== mid || !sameMinute(prev, m);
}
function checksHTML(m){
  return '<svg class="ic"><use href="#' + (m.id <= (S.readOut || 0) ? 'i-checks' : 'i-check') + '"/></svg>';
}
function quoteHTML(r){
  return '<div class="quote" data-jump="' + r.id + '"><b>' + esc(r.name || 'Message') + '</b>' +
    (r.snippet ? '<span>' + esc(r.snippet) + '</span>' : '') + '</div>';
}
function reactionsHTML(m){
  return '<div class="reacts">' + m.reactions.map(function(r){
    return '<span class="react' + (r.me ? ' mine' : '') + '" data-react="' + esc(r.emoji) + '">' +
      esc(r.emoji) + (r.count > 1 ? ' ' + r.count : '') + '</span>';
  }).join('') + '</div>';
}
function webpageHTML(m){
  var wp = m.webpage;
  if (!wp || !wp.url) return '';
  var img = wp.thumb ? '<img loading="lazy" src="api/media/' + S.current + '/' + m.id + '?kind=thumb" onerror="this.remove()">' : '';
  return '<div class="wp" data-url="' + esc(wp.url) + '">' +
    (wp.site ? '<div class="wsite">' + esc(wp.site) + '</div>' : '') +
    (wp.title ? '<div class="wtitle">' + esc(wp.title) + '</div>' : '') +
    (wp.desc ? '<div class="wdesc">' + esc(wp.desc) + '</div>' : '') + img + '</div>';
}
var TINY_GIF = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
function mediaHTML(m){
  var mi = m.media;
  if (!mi) return '';
  var u = 'api/media/' + S.current + '/' + m.id;
  var k = mi.kind;
  if (k === 'photo'){
    var st = (mi.w && mi.h) ? ' style="aspect-ratio:' + mi.w + '/' + mi.h + ';max-height:60vh;object-fit:cover"' : '';
    return '<div class="photo"><img loading="lazy"' + st + ' src="' + u + '?kind=thumb" data-act="view" data-kind="photo" data-full="' + u + '?kind=photo" onerror="this.onerror=null;this.src=this.getAttribute(&quot;data-full&quot;)"></div>';
  }
  if (k === 'video' || k === 'gif' || k === 'videonote'){
    return '<div class="video-wrap" data-act="view" data-kind="' + k + '">' +
      '<img loading="lazy" src="' + u + '?kind=thumb" style="min-width:120px;min-height:90px" onerror="this.onerror=null;this.src=\'' + TINY_GIF + '\'" alt="">' +
      '<span class="play"><svg class="ic big"><use href="#i-play"/></svg></span>' +
      (mi.duration ? '<span class="dur">' + fmtDur(mi.duration) + '</span>' : '') + '</div>';
  }
  if (k === 'voice'){
    return '<div><audio controls preload="none" src="' + u + '?kind=file"></audio>' +
      (mi.duration ? '<div class="fsize">' + fmtDur(mi.duration) + '</div>' : '') + '</div>';
  }
  if (k === 'audio'){
    return '<div style="min-width:220px"><div class="fname">' + esc(mi.title || mi.name || 'Audio') + '</div>' +
      (mi.performer ? '<div class="fsize">' + esc(mi.performer) + '</div>' : '') +
      '<audio controls preload="none" src="' + u + '?kind=file"></audio></div>';
  }
  if (k === 'sticker'){
    return '<div class="sticker"><img loading="lazy" src="' + u + '?kind=thumb" alt="" data-alt="' + esc(mi.alt || '🙂') + '" onerror="this.onerror=null;this.parentNode.textContent=this.getAttribute(&quot;data-alt&quot;)"></div>';
  }
  if (k === 'file' || k === 'picfile'){
    return '<div class="filebox"><div class="ficon"><svg class="ic big"><use href="#i-file"/></svg></div>' +
      '<div style="min-width:0"><div class="fname">' + esc(mi.name || 'File') + '</div>' +
      '<div class="fsize">' + fmtSize(mi.size) + '</div>' +
      '<a class="fdl" href="' + u + '?kind=file&amp;dl=1" download="' + esc(mi.name || ('file_' + m.id)) + '">Download</a></div></div>';
  }
  if (k === 'poll'){
    var opts = (mi.answers || []).map(function(a){
      return '<div class="opt' + (a.chosen ? ' chosen' : '') + '">' + esc(a.text) +
        (a.voters != null ? '<span class="pc">' + a.voters + '</span>' : '') + '</div>';
    }).join('');
    return '<div class="pollbox"><div class="q">' + esc(mi.question || 'Poll') + '</div>' + opts +
      (mi.total ? '<div class="fsize">' + mi.total + ' vote(s)</div>' : '') +
      (mi.closed ? '<div class="fsize">Final results</div>' : '') + '</div>';
  }
  if (k === 'geo'){
    return '<a href="https://maps.google.com/?q=' + encodeURIComponent(mi.lat + ',' + mi.lon) +
      '" target="_blank" rel="noopener noreferrer">📍 ' + (mi.lat || '?') + ', ' + (mi.lon || '?') + '</a>';
  }
  if (k === 'contact'){
    return '<div class="fname">' + esc(mi.label) + '</div><div class="fsize">' + esc(mi.phone || '') + '</div>';
  }
  if (k === 'dice'){
    return '<div style="font-size:44px;text-align:center">' + esc(mi.emoji || '🎲') + ' ' + (mi.value || '') + '</div>';
  }
  return '<div class="fsize">' + esc(mi.label || 'Media') + '</div>';
}
function messageHTML(m, prev){
  if (m.service){
    return '<div class="svc" data-id="' + m.id + '">' + (m.html || 'Service message') + '</div>';
  }
  var out = !!m.out;
  var cls = ['msg', out ? 'out' : 'in'];
  if (isFirstOfGroup(m, prev)) cls.push('first');
  var inner = '';
  if (m.forward_from) inner += '<div class="fwd">Forwarded from ' + esc(m.forward_from) + '</div>';
  var inGroup = S.dlg && (S.dlg.type === 'group');
  if (!out && inGroup && m.sender && m.sender.name){
    inner += '<div class="sender" data-uid="' + m.sender.id + '" style="color:' + colorFor(m.sender.id) + '">' + esc(m.sender.name) + '</div>';
  }
  if (m.reply_to) inner += quoteHTML(m.reply_to);
  inner += mediaHTML(m);
  if (m.webpage) inner += webpageHTML(m);
  if (m.html) inner += '<div class="text">' + m.html + '</div>';
  if (m.reactions && m.reactions.length) inner += reactionsHTML(m);
  inner += '<span class="meta">' + (m.edited ? 'edited ' : '') + esc(fmtClock(m.date)) + (out ? checksHTML(m) : '') + '</span>';
  if (m.views) inner += '<span class="fsize">👁 ' + Number(m.views).toLocaleString() + '</span>';
  var av = '';
  if (!out && inGroup && m.sender){
    av = avatarHTML(m.sender.id, m.sender.name, false, '', colorFor(m.sender.id));
  }
  return '<div class="' + cls.join(' ') + '" data-id="' + m.id + '">' + av +
    '<div class="bub">' + inner + '</div>' +
    '<div class="acts"><button class="abtn" data-a="react" title="React">👍</button>' +
    '<button class="abtn" data-a="menu" title="More"><svg class="ic"><use href="#i-more"/></svg></button></div>' +
    '</div>';
}
function buildListHTML(){
  if (!S.msgs.length) return '<div class="empty-list" style="margin:auto">No messages here yet.<br>Say hello 👋</div>';
  var html = '';
  var prev = null;
  var chipDone = false;
  S.msgs.forEach(function(m){
    var pd = prev && prev.date ? new Date(prev.date) : null;
    var d = m.date ? new Date(m.date) : null;
    if ((d && !pd) || (d && pd && !sameDay(pd, d))){
      html += '<div class="day-chip">' + esc(dayLabel(d)) + '</div>';
    }
    if (!chipDone && !m.out && !m.service && m.id > (S.readIn || 0)){
      html += '<div class="unread-chip">Unread messages</div>';
      chipDone = true;
    }
    html += messageHTML(m, prev);
    prev = m;
  });
  return html;
}
function renderAllMessages(){
  $('#msgs').innerHTML = buildListHTML();
}
function appendNodes(list){
  if (!list.length) return;
  var box = $('#msgs');
  var empty = box.querySelector('.empty-list');
  if (empty) empty.remove();
  var prevIdx = S.msgs.length - list.length - 1;
  var prev = prevIdx >= 0 ? S.msgs[prevIdx] : null;
  var html = '';
  list.forEach(function(m){
    var pd = prev && prev.date ? new Date(prev.date) : null;
    var d = m.date ? new Date(m.date) : null;
    if ((d && !pd) || (d && pd && !sameDay(pd, d))){
      html += '<div class="day-chip">' + esc(dayLabel(d)) + '</div>';
    }
    html += messageHTML(m, prev);
    prev = m;
  });
  box.insertAdjacentHTML('beforeend', html);
}
function replaceMessage(m){
  if (!m || S.msgById[m.id] === undefined) return;
  var old = S.msgById[m.id];
  var i = S.msgs.indexOf(old);
  if (i < 0) return;
  S.msgs[i] = m;
  S.msgById[m.id] = m;
  var prev = i > 0 ? S.msgs[i - 1] : null;
  var node = $('#msgs .msg[data-id="' + m.id + '"]');
  if (node) node.outerHTML = messageHTML(m, prev);
}
function removeMessages(ids){
  var idset = {};
  ids.forEach(function(i){ idset[i] = true; });
  S.msgs = S.msgs.filter(function(m){ return !idset[m.id]; });
  ids.forEach(function(i){
    delete S.msgById[i];
    var node = $('#msgs .msg[data-id="' + i + '"], #msgs .svc[data-id="' + i + '"]');
    if (node) node.remove();
  });
  S.selection = {};
}

/* ============================== scrolling ============================== */
function isNearBottom(){
  var b = $('#msgs');
  return b.scrollHeight - b.scrollTop - b.clientHeight < 160;
}
function scrollBottom(){
  var b = $('#msgs');
  b.scrollTop = b.scrollHeight;
}
function unreadLoadedCount(){
  var n = 0;
  for (var i = S.msgs.length - 1; i >= 0; i--){
    var m = S.msgs[i];
    if (m.id <= (S.readIn || 0)) break;
    if (!m.out && !m.service) n++;
  }
  return n;
}
function updateFab(){
  var fab = $('#scrollDown');
  fab.hidden = S.current == null || isNearBottom();
  var n = unreadLoadedCount();
  var b = $('#sdBadge');
  b.hidden = !n;
  b.textContent = n > 99 ? '99+' : n;
}
$('#msgs').addEventListener('scroll', function(){
  if (this.scrollTop < 80 && S.hasMore && !S.loadingOlder && S.current != null) loadOlder();
  updateFab();
});
$('#scrollDown').addEventListener('click', scrollBottom);
function loadOlder(){
  if (S.loadingOlder || !S.hasMore || S.current == null || !S.oldest) return;
  S.loadingOlder = true;
  var box = $('#msgs');
  var prevH = box.scrollHeight, prevTop = box.scrollTop;
  var chatId = S.current;
  api('api/messages?chat_id=' + chatId + '&offset_id=' + S.oldest + '&limit=50').then(function(r){
    if (S.current !== chatId) return;
    var older = (r.messages || []).filter(function(x){ return S.msgById[x.id] === undefined; }).reverse();
    if (older.length < 50) S.hasMore = false;
    if (older.length){
      older.forEach(function(m){ S.msgs.unshift(m); S.msgById[m.id] = m; });
      S.oldest = older[0].id;
      renderAllMessages();
      box.scrollTop = box.scrollHeight - prevH + prevTop;
    } else { S.hasMore = false; }
  }).catch(toastErr).finally(function(){ S.loadingOlder = false; });
}

/* ============================== polling ============================== */
function startPolling(){
  stopPolling();
  S.pollTimer = setInterval(pollMessages, 8000);
}
function stopPolling(){
  if (S.pollTimer){ clearInterval(S.pollTimer); S.pollTimer = null; }
}
function updateChecks(){
  S.msgs.forEach(function(m){
    if (!m.out) return;
    var node = $('#msgs .msg[data-id="' + m.id + '"] .meta use');
    if (node) node.setAttribute('href', m.id <= (S.readOut || 0) ? '#i-checks' : '#i-check');
  });
}
function updateDialogPreview(m){
  var d = S.dialogById[S.current];
  if (!d || !m) return;
  d.last = {id: m.id, snippet: previewOf(m), date: m.date, out: m.out,
            sender: m.sender ? m.sender.name : null};
  renderDialogs();
}
function pollMessages(){
  var id = S.current;
  if (id == null || document.hidden || S.viewerOpen || !S.newest) return;
  api('api/messages?chat_id=' + id + '&min_id=' + S.newest + '&limit=100').then(function(r){
    if (S.current !== id) return;
    if (typeof r.read_outbox_max_id === 'number') S.readOut = Math.max(S.readOut || 0, r.read_outbox_max_id);
    if (typeof r.read_inbox_max_id === 'number') S.readIn = Math.max(S.readIn || 0, r.read_inbox_max_id);
    var fresh = (r.messages || []).filter(function(x){ return S.msgById[x.id] === undefined; }).reverse();
    if (fresh.length){
      var nearBottom = isNearBottom();
      fresh.forEach(function(m){ S.msgs.push(m); S.msgById[m.id] = m; });
      S.newest = Math.max.apply(null, [S.newest].concat(fresh.map(function(x){ return x.id; })));
      appendNodes(fresh);
      if (nearBottom || fresh[fresh.length - 1].out) scrollBottom();
      updateDialogPreview(fresh[fresh.length - 1]);
    }
    setTyping(r.typing || []);
    updateChecks();
    updateFab();
  }).catch(toastErr);
}
function jumpLatest(){
  if (S.current == null) return;
  var chatId = S.current;
  api('api/messages?chat_id=' + chatId + '&limit=50').then(function(r){
    if (S.current !== chatId) return;
    var list = (r.messages || []).slice().reverse();
    S.msgs = list;
    S.msgById = {};
    list.forEach(function(m){ S.msgById[m.id] = m; });
    S.oldest = list.length ? list[0].id : 0;
    S.newest = list.length ? list[list.length - 1].id : 0;
    S.hasMore = list.length >= 50;
    if (typeof r.read_inbox_max_id === 'number') S.readIn = r.read_inbox_max_id;
    if (typeof r.read_outbox_max_id === 'number') S.readOut = r.read_outbox_max_id;
    renderAllMessages();
    scrollBottom();
  }).catch(toastErr);
}
function jumpTo(id){
  if (!id || S.current == null) return;
  var chatId = S.current;
  api('api/messages?chat_id=' + chatId + '&anchor_id=' + id + '&limit=30').then(function(r){
    if (S.current !== chatId) return;
    var list = (r.messages || []).slice().reverse();
    if (!list.length){ toast('Message not found'); return; }
    S.msgs = list;
    S.msgById = {};
    list.forEach(function(m){ S.msgById[m.id] = m; });
    S.oldest = list[0].id;
    S.newest = list[list.length - 1].id;
    S.hasMore = true;
    if (typeof r.read_inbox_max_id === 'number') S.readIn = Math.max(S.readIn || 0, r.read_inbox_max_id);
    if (typeof r.read_outbox_max_id === 'number') S.readOut = Math.max(S.readOut || 0, r.read_outbox_max_id);
    renderAllMessages();
    var node = $('#msgs .msg[data-id="' + id + '"]');
    if (node){ node.classList.add('flash'); node.scrollIntoView({block: 'center'}); }
    else scrollBottom();
  }).catch(toastErr);
}

/* ============================== composer ============================== */
var input = $('#input');
function autoresize(){
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 132) + 'px';
}
input.addEventListener('input', function(){
  autoresize();
  var now = Date.now();
  if (S.current != null && input.value && now - S.typingSent > 4000){
    S.typingSent = now;
    api('api/typing', {method: 'POST', body: {chat_id: S.current}}).catch(function(){});
  }
});
input.addEventListener('keydown', function(e){
  if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); doSend(); }
});
$('#btnSend').addEventListener('click', doSend);
function setReply(m){
  S.replyTo = {id: m.id};
  S.editing = null;
  $('#editBar').hidden = true;
  $('#replyTitle').textContent = 'Reply to ' + (m.out ? 'yourself' : (m.sender ? m.sender.name : 'message'));
  $('#replySnip').textContent = previewOf(m);
  $('#replyBar').hidden = false;
  input.focus();
}
function clearReply(){ S.replyTo = null; $('#replyBar').hidden = true; }
$('#replyCancel').addEventListener('click', clearReply);
function setEditing(m){
  S.editing = m;
  S.replyTo = null;
  $('#replyBar').hidden = true;
  $('#editSnip').textContent = previewOf(m);
  $('#editBar').hidden = false;
  input.value = m.html || m.raw || '';
  autoresize();
  input.focus();
}
function clearEditing(){ S.editing = null; $('#editBar').hidden = true; }
$('#editCancel').addEventListener('click', function(){ clearEditing(); input.value = ''; autoresize(); });
function doSend(){
  if (S.current == null) return;
  var text = input.value.trim();
  if (S.pendingFile){ doUpload(text); return; }
  if (!text) return;
  if (S.editing){
    var em = S.editing;
    clearEditing();
    input.value = ''; autoresize();
    api('api/edit', {method: 'POST', body: {chat_id: S.current, msg_id: em.id, text: text}})
      .then(function(r){ if (r.message) replaceMessage(r.message); })
      .catch(function(e){ toastErr(e); });
    return;
  }
  var replyTo = S.replyTo ? S.replyTo.id : null;
  input.value = ''; autoresize();
  clearReply();
  api('api/send', {method: 'POST', body: {chat_id: S.current, text: text, reply_to: replyTo}})
    .then(function(r){
      if (r.message && S.msgById[r.message.id] === undefined){
        S.msgs.push(r.message);
        S.msgById[r.message.id] = r.message;
        if (r.message.id > (S.newest || 0)) S.newest = r.message.id;
        appendNodes([r.message]);
        scrollBottom();
        updateDialogPreview(r.message);
      }
    })
    .catch(function(e){
      toastErr(e);
      input.value = text;
      autoresize();
    });
}
/* file upload */
$('#btnAttach').addEventListener('click', function(){ $('#fileInput').click(); });
$('#fileInput').addEventListener('change', function(){
  var f = this.files && this.files[0];
  if (!f) return;
  S.pendingFile = f;
  $('#attachName').textContent = f.name + ' (' + fmtSize(f.size) + ')';
  $('#attachIcon').textContent = (f.type && f.type.indexOf('image/') === 0) ? '🖼' : '📎';
  $('#attachBar').hidden = false;
  this.value = '';
});
function clearPendingFile(){ S.pendingFile = null; $('#attachBar').hidden = true; }
$('#attachCancel').addEventListener('click', clearPendingFile);
function doUpload(caption){
  var f = S.pendingFile;
  if (!f || S.current == null) return;
  clearPendingFile();
  input.value = ''; autoresize();
  var fd = new FormData();
  fd.append('chat_id', S.current);
  fd.append('caption', caption || '');
  fd.append('file', f, f.name);
  toast('Uploading ' + f.name + '…');
  api('api/upload', {method: 'POST', form: fd}).then(function(r){
    if (r.message && S.msgById[r.message.id] === undefined){
      S.msgs.push(r.message);
      S.msgById[r.message.id] = r.message;
      if (r.message.id > (S.newest || 0)) S.newest = r.message.id;
      appendNodes([r.message]);
      scrollBottom();
      updateDialogPreview(r.message);
    }
  }).catch(toastErr);
}
/* mark read (manual, never automatic) */
$('#btnMarkRead').addEventListener('click', markRead);
function markRead(){
  if (S.current == null) return;
  api('api/read', {method: 'POST', body: {chat_id: S.current, max_id: S.newest || null}}).then(function(){
    if (S.dlg) S.dlg.unread = 0;
    S.readIn = Math.max(S.readIn || 0, S.newest || 0);
    $$('#msgs .unread-chip').forEach(function(c){ c.remove(); });
    renderDialogs();
    updateFab();
    toast('Marked as read');
  }).catch(toastErr);
}

/* ============================== selection ============================== */
function enterSelecting(){
  S.selecting = true;
  S.selection = {};
  $('#selBar').hidden = false;
  $$('#msgs .msg').forEach(function(n){ n.classList.add('selecting'); });
}
function exitSelecting(){
  S.selecting = false;
  S.selection = {};
  $('#selBar').hidden = true;
  $$('#msgs .msg').forEach(function(n){ n.classList.remove('selecting', 'selected'); });
}
function toggleSelect(id){
  if (S.selection[id]) delete S.selection[id];
  else S.selection[id] = true;
  var node = $('#msgs .msg[data-id="' + id + '"]');
  if (node) node.classList.toggle('selected', !!S.selection[id]);
  $('#selCount').textContent = Object.keys(S.selection).length + ' selected';
}
$('#selCancel').addEventListener('click', exitSelecting);
$('#selForward').addEventListener('click', function(){
  var ids = Object.keys(S.selection).map(Number);
  if (!ids.length) return;
  exitSelecting();
  openForwardPicker(ids);
});
$('#selDelete').addEventListener('click', function(){
  var ids = Object.keys(S.selection).map(Number);
  if (!ids.length) return;
  exitSelecting();
  actDelete(ids);
});

/* ============================== menus ============================== */
var menuActions = {};
function closeMenus(){
  $$('.menu').forEach(function(m){ m.remove(); });
  document.removeEventListener('click', menuOutside, true);
}
function menuOutside(e){
  if (!e.target.closest('.menu')) closeMenus();
}
function openMenu(items, x, y){
  closeMenus();
  menuActions = {};
  var m = document.createElement('div');
  m.className = 'menu';
  var html = '';
  items.forEach(function(it, idx){
    if (it.reactions){
      html += '<div class="rx">' + it.reactions.list.map(function(em){
        return '<button data-rem="' + esc(em) + '">' + esc(em) + '</button>';
      }).join('') + '</div><div class="sep"></div>';
    } else if (it.sep){
      html += '<div class="sep"></div>';
    } else {
      html += '<button data-mi="' + idx + '"' + (it.danger ? ' class="danger"' : '') + '>' +
        (it.icon ? '<svg class="ic"><use href="#' + it.icon + '"/></svg>' : '') +
        '<span>' + esc(it.label) + '</span></button>';
      menuActions[idx] = it.fn || null;
    }
  });
  m.innerHTML = html;
  document.body.appendChild(m);
  var r = m.getBoundingClientRect();
  m.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px';
  document.addEventListener('click', menuOutside, true);
  m.addEventListener('click', function(e){
    var rb = e.target.closest('[data-rem]');
    if (rb){
      var em = rb.getAttribute('data-rem');
      var rItem = items.filter(function(it){ return it.reactions && it.reactions.list.indexOf(em) >= 0; })[0];
      closeMenus();
      if (rItem) rItem.reactions.fn(em);
      return;
    }
    var b = e.target.closest('[data-mi]');
    if (b){
      var fn = menuActions[b.getAttribute('data-mi')];
      closeMenus();
      if (fn) fn();
    }
  });
}
function actReact(m, emoji, mine){
  if (!m || S.current == null) return;
  api('api/react', {method: 'POST', body: {chat_id: S.current, msg_id: m.id, emoji: mine ? '' : emoji}})
    .then(function(r){ if (r.message) replaceMessage(r.message); })
    .catch(toastErr);
}
function actPin(m, pinned){
  api('api/pin', {method: 'POST', body: {chat_id: S.current, msg_id: m.id, pinned: !!pinned}})
    .then(function(){ toast(pinned ? 'Pinned' : 'Unpinned'); })
    .catch(toastErr);
}
function actDelete(ids){
  openConfirm('Delete ' + (ids.length > 1 ? ids.length + ' messages' : 'message') + '?', 'Delete', function(){
    api('api/delete', {method: 'POST', body: {chat_id: S.current, msg_ids: ids, revoke: true}})
      .then(function(){ removeMessages(ids); toast('Deleted'); })
      .catch(toastErr);
  });
}
function openMsgMenu(m, x, y){
  if (!m) return;
  var items = [];
  items.push({reactions: {list: ['👍','👎','❤️','🔥','🎉','😂','😮','😢','🙏'],
                          fn: function(em){ actReact(m, em, false); }}});
  if (!m.service){
    items.push({icon: 'i-reply', label: 'Reply', fn: function(){ setReply(m); }});
    if (m.out && (m.raw || m.html)) items.push({icon: 'i-edit', label: 'Edit', fn: function(){ setEditing(m); }});
    if (m.raw) items.push({icon: 'i-copy', label: 'Copy text', fn: function(){
      try { navigator.clipboard.writeText(m.raw); toast('Copied'); }
      catch (err) { toast('Could not copy'); }
    }});
    items.push({icon: 'i-forward', label: 'Forward', fn: function(){ openForwardPicker([m.id]); }});
    items.push({icon: 'i-pin', label: 'Pin message', fn: function(){ actPin(m, true); }});
    items.push({icon: 'i-pin', label: 'Unpin message', fn: function(){ actPin(m, false); }});
  }
  items.push({icon: 'i-select', label: 'Select', fn: function(){ enterSelecting(); toggleSelect(m.id); }});
  items.push({sep: true});
  items.push({icon: 'i-trash', label: 'Delete', danger: true, fn: function(){ actDelete([m.id]); }});
  openMenu(items, x, y);
}
$('#btnChatMenu').addEventListener('click', function(e){
  e.stopPropagation();
  var r = this.getBoundingClientRect();
  var d = S.dlg || {};
  openMenu([
    {icon: d.muted ? 'i-bell' : 'i-bell-off', label: d.muted ? 'Unmute' : 'Mute', fn: toggleMute},
    {icon: d.archived ? 'i-unarchive' : 'i-archive', label: d.archived ? 'Unarchive' : 'Archive', fn: toggleArchive},
    {sep: true},
    {icon: 'i-checks', label: 'Mark as read', fn: markRead},
    {icon: 'i-down', label: 'Jump to latest', fn: jumpLatest},
  ], r.right - 210, r.bottom + 6);
});
function toggleMute(){
  var d = S.dlg;
  if (!d) return;
  api('api/mute', {method: 'POST', body: {chat_id: d.id, muted: !d.muted}}).then(function(){
    d.muted = !d.muted;
    renderDialogs();
    toast(d.muted ? 'Muted' : 'Unmuted');
  }).catch(toastErr);
}
function toggleArchive(){
  var d = S.dlg;
  if (!d) return;
  var was = d.archived;
  api('api/archive', {method: 'POST', body: {chat_id: d.id, archived: !was}}).then(function(){
    toast(was ? 'Unarchived' : 'Archived');
    loadDialogs(true);
  }).catch(toastErr);
}
$('#btnMenu').addEventListener('click', function(e){
  e.stopPropagation();
  var r = this.getBoundingClientRect();
  var dark = document.documentElement.getAttribute('data-theme') !== 'light';
  openMenu([
    {icon: 'i-saved', label: 'Saved Messages', fn: openSaved},
    {icon: dark ? 'i-sun' : 'i-moon', label: dark ? 'Light theme' : 'Dark theme', fn: toggleTheme},
    {sep: true},
    {icon: 'i-lock', label: 'Lock site', fn: lockSite},
    {icon: 'i-logout', label: 'Log out Telegram', danger: true, fn: doLogout},
  ], r.left, r.bottom + 6);
});
function openSaved(){
  var d = S.dialogs.filter(function(x){ return x.is_self; })[0];
  if (d) openChat(d.id);
  else toast('Saved Messages is not in the dialog list yet');
}
function toggleTheme(){
  var cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', cur);
  try { localStorage.setItem('tgw_theme', cur); } catch (e) {}
}
function lockSite(){
  api('api/site_logout', {method: 'POST'}).catch(function(){}).finally(function(){
    stopPolling();
    if (S.dlgTimer) clearInterval(S.dlgTimer);
    showScreen('site');
  });
}
function doLogout(){
  openConfirm('Log out from Telegram?', 'Log out', function(){
    api('api/tg/logout', {method: 'POST'}).then(function(){
      stopPolling();
      if (S.dlgTimer) clearInterval(S.dlgTimer);
      S.dialogs = [];
      S.dialogById = {};
      closeChat();
      showTgLogin(null);
    }).catch(toastErr);
  });
}

/* ============================== message events ============================== */
$('#msgs').addEventListener('click', function(e){
  if (suppressClick){ suppressClick = false; return; }
  var el = e.target;
  var spoiler = el.closest('.spoiler');
  if (spoiler){ spoiler.classList.toggle('revealed'); return; }
  var msgRow = el.closest('.msg');
  if (S.selecting && msgRow){ toggleSelect(Number(msgRow.dataset.id)); return; }
  var quote = el.closest('.quote');
  if (quote){ jumpTo(Number(quote.dataset.jump)); return; }
  var react = el.closest('.react');
  if (react && msgRow){
    var m = S.msgById[msgRow.dataset.id];
    actReact(m, react.getAttribute('data-react'), react.classList.contains('mine'));
    return;
  }
  var wp = el.closest('.wp');
  if (wp && wp.dataset.url){ window.open(wp.dataset.url, '_blank', 'noopener'); return; }
  var who = el.closest('[data-uid]');
  if (who){
    var uid = Number(who.dataset.uid);
    if (uid && (!S.me || uid !== S.me.id)) openChat(uid);
    return;
  }
  var view = el.closest('[data-act="view"]');
  if (view && msgRow){ openViewer(Number(msgRow.dataset.id), view.dataset.kind); return; }
  var act = el.closest('.abtn');
  if (act && msgRow){
    var m2 = S.msgById[msgRow.dataset.id];
    if (act.dataset.a === 'react') actReact(m2, '👍', false);
    else openMsgMenu(m2, act.getBoundingClientRect().left, act.getBoundingClientRect().bottom + 4);
    return;
  }
});
$('#msgs').addEventListener('play', function(e){
  $$('audio', $('#msgs')).forEach(function(a){ if (a !== e.target) a.pause(); });
}, true);
var lpTimer = null, suppressClick = false;
$('#msgs').addEventListener('touchstart', function(e){
  var row = e.target.closest('.msg,.svc');
  if (!row || !row.dataset.id) return;
  var t = e.touches[0];
  var x = t.clientX, y = t.clientY;
  var rid = row.dataset.id;
  lpTimer = setTimeout(function(){
    lpTimer = null;
    suppressClick = true;
    if (navigator.vibrate){ try { navigator.vibrate(10); } catch (ex) {} }
    openMsgMenu(S.msgById[rid], x, y);
  }, 480);
}, {passive: true});
['touchend', 'touchmove', 'touchcancel'].forEach(function(ev){
  $('#msgs').addEventListener(ev, function(){
    if (lpTimer){ clearTimeout(lpTimer); lpTimer = null; }
  }, {passive: true});
});
$('#msgs').addEventListener('contextmenu', function(e){
  var row = e.target.closest('.msg,.svc');
  if (!row || !row.dataset.id) return;
  e.preventDefault();
  openMsgMenu(S.msgById[row.dataset.id], e.clientX, e.clientY);
});

/* ============================== modal helpers ============================== */
function openModal(html, wide){
  var box = $('#modalBox');
  box.className = wide ? 'wide' : '';
  box.innerHTML = html;
  $('#modal').hidden = false;
}
function closeModal(){
  $('#modal').hidden = true;
  $('#modalBox').innerHTML = '';
}
$('#modal').addEventListener('click', function(e){ if (e.target === this) closeModal(); });
function openConfirm(title, okLabel, fn){
  openModal('<div class="mhead"><b>' + esc(title) + '</b></div>' +
    '<div class="mbody" style="display:flex;gap:8px;justify-content:flex-end">' +
    '<button class="btn" id="cfNo">Cancel</button>' +
    '<button class="btn danger" id="cfYes">' + esc(okLabel) + '</button></div>');
  $('#cfNo').onclick = closeModal;
  $('#cfYes').onclick = function(){ closeModal(); fn(); };
}
function openForwardPicker(ids){
  openModal('<div class="mhead"><b>Forward to…</b>' +
    '<button class="icon-btn" id="fpClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div style="padding:8px 12px 0"><input id="fpSearch" placeholder="Search chats" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);outline:none"></div>' +
    '<div class="mbody" id="fpList"></div>', true);
  $('#fpClose').onclick = closeModal;
  function render(q){
    var list = S.dialogs.filter(function(d){ return !q || d.name.toLowerCase().indexOf(q) >= 0; }).slice(0, 80);
    $('#fpList').innerHTML = list.map(function(d){
      return '<div class="rowitem" data-fid="' + d.id + '">' + avatarHTML(d.id, d.name, d.has_photo, 'small') +
        '<div class="ri-body"><div class="ri-t">' + esc(d.name) + '</div></div></div>';
    }).join('') || '<div class="empty-list">No chats</div>';
  }
  render('');
  $('#fpSearch').oninput = function(){ render(this.value.trim().toLowerCase()); };
  $('#fpList').onclick = function(e){
    var row = e.target.closest('[data-fid]');
    if (!row) return;
    var to = Number(row.dataset.fid);
    var from = S.current;
    closeModal();
    api('api/forward', {method: 'POST', body: {from_chat_id: from, msg_ids: ids, to_chat_id: to}})
      .then(function(){ toast('Forwarded'); openChat(to); })
      .catch(toastErr);
  };
}
/* ---------- media gallery ---------- */
$('#btnGallery').addEventListener('click', function(){ if (S.current != null) openGallery(); });
function openGallery(){
  S.galleryTab = 'media';
  S.galleryOffset = 0;
  S.galleryDone = false;
  var tabs = [['media', 'Photos & videos'], ['files', 'Files'], ['voice', 'Voice'], ['links', 'Links']];
  openModal('<div class="mhead"><b>Media</b>' +
    '<button class="icon-btn" id="galClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div class="mtabs" id="galTabs">' + tabs.map(function(t){
      return '<button data-gt="' + t[0] + '" class="' + (t[0] === 'media' ? 'on' : '') + '">' + esc(t[1]) + '</button>';
    }).join('') + '</div>' +
    '<div class="mbody"><div class="gal-grid" id="galGrid"></div>' +
    '<div id="galMore" style="padding:10px;text-align:center"><button class="btn" id="galMoreBtn">Load more</button></div></div>', true);
  $('#galClose').onclick = closeModal;
  $('#galTabs').onclick = function(e){
    var b = e.target.closest('[data-gt]');
    if (!b) return;
    S.galleryTab = b.dataset.gt;
    S.galleryOffset = 0;
    S.galleryDone = false;
    $$('#galTabs button').forEach(function(x){ x.classList.toggle('on', x === b); });
    $('#galGrid').innerHTML = '';
    loadGallery();
  };
  $('#galMoreBtn').onclick = function(){ loadGallery(); };
  loadGallery();
}
function loadGallery(){
  var tab = S.galleryTab;
  api('api/gallery?chat_id=' + S.current + '&tab=' + tab + '&limit=60' +
      (S.galleryOffset ? '&offset_id=' + S.galleryOffset : '')).then(function(r){
    var grid = $('#galGrid');
    if (!grid) return;
    var msgs = r.messages || [];
    if (msgs.length < 60) S.galleryDone = true;
    if (msgs.length) S.galleryOffset = msgs[msgs.length - 1].id;
    var html = '';
    msgs.forEach(function(m){
      if (tab === 'media'){
        var mi = m.media;
        if (!mi || ['photo', 'video', 'gif', 'videonote'].indexOf(mi.kind) < 0) return;
        html += '<div class="gal-item" data-gview="' + m.id + '" data-gkind="' + mi.kind + '">' +
          '<img loading="lazy" src="api/media/' + S.current + '/' + m.id + '?kind=thumb" onerror="this.remove()">' +
          (mi.duration && mi.kind !== 'photo' ? '<span class="vdur">' + fmtDur(mi.duration) + '</span>' : '') + '</div>';
      } else if (tab === 'files'){
        var mi2 = m.media;
        if (!mi2 || !mi2.name) return;
        html += '<div class="rowitem"><div class="ficon" style="width:36px;height:36px;border-radius:8px;background:var(--accent);color:#fff;display:grid;place-items:center;flex:none"><svg class="ic"><use href="#i-file"/></svg></div>' +
          '<div class="ri-body"><div class="ri-t">' + esc(mi2.name) + '</div><div class="ri-s">' + fmtSize(mi2.size) + '</div></div>' +
          '<a class="fdl" href="api/media/' + S.current + '/' + m.id + '?kind=file&amp;dl=1" download="' + esc(mi2.name) + '"><svg class="ic"><use href="#i-dl"/></svg></a></div>';
      } else if (tab === 'voice'){
        if (!m.media || m.media.kind !== 'voice') return;
        html += '<div class="rowitem"><svg class="ic"><use href="#i-mic"/></svg>' +
          '<div class="ri-body"><div class="ri-s">' + esc(fmtListTime(m.date)) + '</div></div>' +
          '<audio controls preload="none" src="api/media/' + S.current + '/' + m.id + '?kind=file" style="flex:1;min-width:0"></audio></div>';
      } else if (tab === 'links'){
        var wp = m.webpage;
        var url = wp ? wp.url : (m.raw || '');
        var title = wp ? (wp.title || wp.site || url) : url;
        if (!url) return;
        html += '<div class="rowitem" data-lurl="' + esc(url) + '"><svg class="ic"><use href="#i-forward"/></svg>' +
          '<div class="ri-body"><div class="ri-t" style="color:var(--link)">' + esc(title) + '</div>' +
          '<div class="ri-s">' + esc(url) + '</div></div></div>';
      }
    });
    grid.insertAdjacentHTML('beforeend', html);
    var more = $('#galMore');
    if (more) more.hidden = S.galleryDone;
  }).catch(toastErr);
}
$('#modalBox').addEventListener('click', function(e){
  var gv = e.target.closest('[data-gview]');
  if (gv){ openViewer(Number(gv.dataset.gview), gv.dataset.gkind); return; }
  var lu = e.target.closest('[data-lurl]');
  if (lu){ window.open(lu.dataset.lurl, '_blank', 'noopener'); return; }
});
/* ---------- members ---------- */
$('#btnMembers').addEventListener('click', function(){ if (S.current != null) openMembers(); });
function openMembers(){
  openModal('<div class="mhead"><b>Members</b>' +
    '<button class="icon-btn" id="mbClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div style="padding:8px 12px 0"><input id="mbSearch" placeholder="Search members" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);outline:none"></div>' +
    '<div class="mbody" id="mbList"><div class="spinner"></div></div>' +
    '<div class="muted" id="mbNote" style="padding:0 14px 12px;font-size:13px"></div>', true);
  $('#mbClose').onclick = closeModal;
  var tmr = null;
  function load(q){
    api('api/members?chat_id=' + S.current + (q ? '&q=' + encodeURIComponent(q) : '')).then(function(r){
      var box = $('#mbList');
      if (!box) return;
      $('#mbNote').textContent = r.note || '';
      box.innerHTML = (r.members || []).map(function(u){
        return '<div class="rowitem" data-uid="' + u.id + '">' + avatarHTML(u.id, u.name, u.has_photo, 'small') +
          '<div class="ri-body"><div class="ri-t">' + esc(u.name) +
          (u.role ? '<span class="tag">' + esc(u.role) + '</span>' : '') + '</div>' +
          '<div class="ri-s">' + esc(u.status || (u.bot ? 'bot' : (u.username ? '@' + u.username : ''))) + '</div></div></div>';
      }).join('') || '<div class="empty-list">Nobody here</div>';
    }).catch(function(e){
      var box = $('#mbList');
      if (box) box.innerHTML = '';
      var n = $('#mbNote');
      if (n) n.textContent = (e && e.detail) || 'Could not load members';
    });
  }
  load('');
  $('#mbSearch').oninput = function(){
    var q = this.value.trim();
    clearTimeout(tmr);
    tmr = setTimeout(function(){ load(q); }, 350);
  };
  $('#mbList').onclick = function(e){
    var row = e.target.closest('[data-uid]');
    if (!row) return;
    var uid = Number(row.dataset.uid);
    closeModal();
    if (S.dialogById[uid]) openChat(uid);
    else { toast('Opening chat…'); loadDialogs(false).then(function(){ openChat(uid); }); }
  };
}
/* ---------- in-chat search ---------- */
function openCSearch(){
  if (S.current == null) return;
  S.csearchOpen = true;
  $('#csearchBar').hidden = false;
  $('#csearchResults').innerHTML = '';
  $('#csearchInput').value = '';
  $('#csearchInput').focus();
}
function closeCSearch(){
  S.csearchOpen = false;
  $('#csearchBar').hidden = true;
}
$('#btnCSearch').addEventListener('click', openCSearch);
$('#csearchClose').addEventListener('click', closeCSearch);
var csTimer = null;
$('#csearchInput').addEventListener('input', function(){
  var q = this.value.trim();
  clearTimeout(csTimer);
  if (!q){ $('#csearchResults').innerHTML = ''; return; }
  csTimer = setTimeout(function(){
    api('api/search?chat_id=' + S.current + '&q=' + encodeURIComponent(q)).then(function(r){
      $('#csearchResults').innerHTML = (r.messages || []).slice(0, 60).map(function(m){
        return '<div class="search-hit" data-sh="' + m.id + '">' +
          '<div class="sh-t">' + esc(m.sender ? m.sender.name : (m.out ? 'You' : '')) + ' · ' + esc(fmtListTime(m.date)) + '</div>' +
          '<div class="sh-b">' + (m.html || esc(previewOf(m))) + '</div></div>';
      }).join('') || '<div class="empty-list">Nothing found</div>';
    }).catch(toastErr);
  }, 350);
});
$('#csearchResults').addEventListener('click', function(e){
  var hit = e.target.closest('[data-sh]');
  if (!hit) return;
  jumpTo(Number(hit.dataset.sh));
  if (window.innerWidth < 900) closeCSearch();
});
/* ---------- media viewer ---------- */
function openViewer(msgId, kind){
  var m = S.msgById[msgId];
  if (!m || S.current == null) return;
  var u = 'api/media/' + S.current + '/' + m.id;
  var name = (m.media && (m.media.name || m.media.title)) || ('media_' + m.id);
  var v = $('#viewer');
  var mediaHtml;
  if (kind === 'photo'){
    mediaHtml = '<img src="' + u + '?kind=photo" alt="">';
  } else {
    var attrs = kind === 'gif' ? ' autoplay muted loop playsinline' : ' autoplay controls playsinline';
    mediaHtml = '<video src="' + u + '?kind=file"' + attrs + '></video>';
  }
  v.innerHTML = '<div class="vtop">' +
    '<button class="icon-btn" id="vClose" title="Close"><svg class="ic"><use href="#i-close"/></svg></button>' +
    '<b>' + esc(name) + '</b>' +
    '<a class="icon-btn" href="' + u + '?kind=file&amp;dl=1" download="' + esc(name) + '" title="Download"><svg class="ic"><use href="#i-dl"/></svg></a></div>' +
    '<div class="vbody">' + mediaHtml + '</div>';
  v.hidden = false;
  S.viewerOpen = true;
  $('#vClose').onclick = closeViewer;
  v.onclick = function(e){ if (e.target === v || e.target.classList.contains('vbody')) closeViewer(); };
}
function closeViewer(){
  var v = $('#viewer');
  v.innerHTML = '';
  v.hidden = true;
  S.viewerOpen = false;
}
/* ---------- keyboard shortcuts ---------- */
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape'){
    if (S.viewerOpen){ closeViewer(); return; }
    if (!$('#modal').hidden){ closeModal(); return; }
    if ($$('.menu').length){ closeMenus(); return; }
    if (S.selecting){ exitSelecting(); return; }
    if (S.csearchOpen){ closeCSearch(); return; }
    if (S.current != null){ closeChat(); return; }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')){
    e.preventDefault();
    document.body.classList.remove('chat-open');
    var q = $('#dlgSearch');
    q.focus();
    q.select();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F')){
    if (S.current != null){ e.preventDefault(); openCSearch(); }
  }
});
/* go */
boot();
})();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# index / manifest / icons / (healthz defined above)
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index():
    return HTMLResponse(INDEX_HTML)


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return JSONResponse({
        "name": "TgWeb — Telegram Web",
        "short_name": "TgWeb",
        "description": "Self-hosted single-user Telegram web client",
        "start_url": ".",
        "scope": ".",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#3390ec",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/icon-192.png", include_in_schema=False)
async def icon192():
    return Response(ICON_192, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/icon-512.png", include_in_schema=False)
async def icon512():
    return Response(ICON_512, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
