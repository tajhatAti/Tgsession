"""Serialization: Telethon dialogs/messages/members -> JSON-safe dicts.
Every field is null-safe: one broken message must never break the list."""
import base64
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from telethon import types

from .config import log
from .formatting import entities_to_html
from .util import _ESC, iso, listify

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


def buttons_json(m) -> Optional[List[List[dict]]]:
    """Inline keyboard buttons shown under a (bot) message."""
    try:
        rm = getattr(m, "reply_markup", None)
        if not isinstance(rm, types.ReplyInlineMarkup):
            return None
        rows = []
        for row in (rm.rows or []):
            btns = []
            for b in (getattr(row, "buttons", None) or []):
                t = getattr(b, "type", None)
                item = {"text": getattr(b, "text", "") or ""}
                if isinstance(t, types.InlineButtonTypeUrl):
                    item["url"] = getattr(t, "url", None)
                elif isinstance(t, types.InlineButtonTypeUrlAuth):
                    item["url"] = getattr(t, "url", None)
                elif isinstance(t, types.InlineButtonTypeWebView):
                    item["url"] = getattr(t, "url", None)
                    item["webview"] = True
                elif isinstance(t, types.InlineButtonTypeCallback):
                    try:
                        item["data"] = base64.b64encode(t.data or b"").decode("ascii")
                    except Exception:
                        item["data"] = ""
                elif isinstance(t, types.InlineButtonTypeSwitchInline):
                    item["switch"] = getattr(t, "query", "") or ""
                elif isinstance(t, types.InlineButtonTypeCopy):
                    item["copy"] = getattr(t, "copy_text", "") or ""
                elif isinstance(t, types.InlineButtonTypeGame):
                    item["game"] = True
                btns.append(item)
            if btns:
                rows.append(btns)
        return rows or None
    except Exception:
        return None


def keyboard_json(m) -> Optional[List[List[str]]]:
    """Bot reply keyboard (shown above the composer while the bot chat is open)."""
    try:
        rm = getattr(m, "reply_markup", None)
        if not isinstance(rm, types.ReplyKeyboardMarkup):
            return None
        rows = []
        for row in (rm.rows or []):
            texts = [getattr(b, "text", "") or "" for b in (getattr(row, "buttons", None) or [])]
            if texts:
                rows.append(texts)
        return rows or None
    except Exception:
        return None


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
            "buttons": buttons_json(m),
            "keyboard": keyboard_json(m),
            "grouped_id": getattr(m, "grouped_id", None),
        }
    except Exception as e:
        log.warning("failed to serialize message %s: %s", mid, e)
        return {"id": mid, "chat_id": chat_id, "date": None, "out": False, "sender": None,
                "html": "", "raw": "", "service": True, "reply_to": None, "media": None,
                "webpage": None, "reactions": None, "edited": False, "views": None,
                "forward_from": None, "buttons": None, "keyboard": None, "grouped_id": None, "error": True}


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
