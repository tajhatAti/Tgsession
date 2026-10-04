"""Chat endpoints: dialogs, messages (paging), search, gallery, members,
send/edit/delete/forward, read/typing, reactions, pin/mute/archive, upload."""
from datetime import datetime
from typing import List, Optional

import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from telethon import functions, types
from telethon import errors as tg_errors
from telethon.utils import get_input_channel, get_input_user, get_peer_id

from ..config import MAX_UPLOAD, log
from ..schemas import (ArchiveBody, CallbackBody, DeleteBody, EditBody, ForwardBody, MuteBody,
                       PinBody, ReactBody, ReadBody, SendBody, TypingBody)
from ..serialize import (dialog_to_json, display_name, message_to_json, participant_json,
                         serialize_messages, user_status_text)
from ..security import require_site
from ..tgstate import require_client, resolve_entity, state
from ..updates import typing_names
from ..util import listify

router = APIRouter(prefix="/api", dependencies=[Depends(require_site)])


# ---- dialogs ---------------------------------------------------------------

@router.get("/dialogs")
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


@router.get("/messages")
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
    pinned = None
    try:
        pm = await client.get_messages(entity, limit=1, filter=types.InputMessagesFilterPinned)
        pm = listify(pm)
        if pm and pm[0] is not None:
            pinned = {"id": int(pm[0].id), "raw": (pm[0].message or "")[:80] or "📎 Pinned media"}
    except Exception as e:
        log.warning("pinned lookup failed: %s", e)
    return {"messages": data, "typing": typing_names(chat_id), "pinned": pinned, **(info or {})}


@router.get("/search")
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


@router.get("/gallery")
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

@router.get("/members")
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

@router.post("/send")
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


@router.post("/edit")
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


@router.post("/delete")
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


@router.post("/forward")
async def api_forward(body: ForwardBody):
    client = await require_client()
    src = await resolve_entity(client, body.from_chat_id)
    dst = await resolve_entity(client, body.to_chat_id)
    ids = [i for i in body.msg_ids if i]
    if not ids:
        raise HTTPException(400, "No messages selected")
    try:
        await client.forward_messages(dst, ids, from_peer=src,
                                      drop_author=bool(getattr(body, "hide_sender", False)))
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@router.post("/read")
async def api_read(body: ReadBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    try:
        await client.send_read_acknowledge(entity, max_id=body.max_id or None, clear_mentions=True)
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@router.post("/typing")
async def api_typing(body: TypingBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    try:
        await client(functions.messages.SetTypingRequest(
            peer=entity, action=types.SendMessageTypingAction()))
    except tg_errors.RPCError:
        pass  # typing is best-effort
    return {"ok": True}


@router.post("/react")
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


@router.post("/pin")
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


@router.post("/mute")
async def api_mute(body: MuteBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    until = 2147483647 if body.muted else 0
    await client(functions.account.UpdateNotifySettingsRequest(
        peer=types.InputNotifyPeer(peer=entity),
        settings=types.InputPeerNotifySettings(mute_until=until)))
    return {"ok": True}


@router.post("/archive")
async def api_archive(body: ArchiveBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    await client(functions.folders.EditPeerFoldersRequest(
        folder_peers=[types.InputFolderPeer(peer=entity, folder_id=1 if body.archived else 0)]))
    return {"ok": True}


# ---- upload ------------------------------------------------------------------

@router.post("/upload")
async def api_upload(chat_id: int = Form(...), caption: str = Form(""),
                     voice: int = Form(0), duration: int = Form(0),
                     file: UploadFile = File(...)):
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    data = await file.read(MAX_UPLOAD + 1)
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (limit is 2 GB)")
    extra = {}
    if voice:
        # recorded voice note from the browser
        attrs = [types.DocumentAttributeAudio(duration=max(0, int(duration or 0)), voice=True,
                                              waveform=b"\x02" * 63)]
        extra = {"attributes": attrs, "mime_type": (file.content_type or "audio/ogg")}
    try:
        msg = await client.send_file(entity, data, file_name=file.filename or "file",
                                     caption=(caption or None), force_document=False, **extra)
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    data_j = await serialize_messages(client, entity, [msg], chat_id)
    return {"message": data_j[0] if data_j else None}


# ---- profile (user / group / channel info) ----------------------------------
@router.get("/profile")
async def api_profile(chat_id: int):
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    try:
        e = await client.get_entity(entity)
    except Exception:
        e = None
    out = {"id": chat_id, "type": "chat", "name": None, "username": None, "phone": None,
           "bio": None, "status": None, "verified": False, "premium": False,
           "is_self": False, "is_bot": False, "has_photo": False,
           "members": None, "online": None, "common": None, "link": None}
    try:
        if isinstance(e, types.User):
            is_bot = bool(getattr(e, "bot", False))
            is_self = bool(getattr(e, "is_self", False) or getattr(e, "self", False))
            out.update({"type": "bot" if is_bot else "user", "is_bot": is_bot,
                        "is_self": is_self, "name": display_name(e),
                        "username": getattr(e, "username", None),
                        "phone": getattr(e, "phone", None),
                        "status": None if is_bot else user_status_text(e),
                        "verified": bool(getattr(e, "verified", False)),
                        "premium": bool(getattr(e, "premium", False)),
                        "has_photo": getattr(e, "photo", None) is not None})
            try:
                res = await client(functions.users.GetFullUserRequest(id=get_input_user(e)))
                fu = getattr(res, "full_user", None)
                if fu is not None:
                    out["bio"] = getattr(fu, "about", None)
                    out["common"] = getattr(fu, "common_chats_count", None)
                    if getattr(res, "users", None):
                        fresh = next((u for u in res.users if getattr(u, "id", None) == e.id), e)
                        out["status"] = None if is_bot else user_status_text(fresh)
                        out["premium"] = bool(getattr(fresh, "premium", False))
                        out["has_photo"] = getattr(fresh, "photo", None) is not None
            except tg_errors.RPCError:
                pass
            except Exception:
                pass
        elif isinstance(e, types.Channel):
            out.update({"type": "channel" if e.broadcast else "group",
                        "name": display_name(e),
                        "username": getattr(e, "username", None),
                        "verified": bool(getattr(e, "verified", False)),
                        "has_photo": getattr(e, "photo", None) is not None,
                        "members": getattr(e, "participants_count", None)})
            try:
                res = await client(functions.channels.GetFullChannelRequest(
                    channel=get_input_channel(e)))
                fc = getattr(res, "full_chat", None)
                if fc is not None:
                    out["bio"] = getattr(fc, "about", None)
                    out["members"] = getattr(fc, "participants_count", None)
                    out["online"] = getattr(fc, "online_count", None)
            except Exception:
                pass
        elif isinstance(e, types.Chat):
            out.update({"type": "group", "name": display_name(e),
                        "has_photo": getattr(e, "photo", None) is not None,
                        "members": getattr(e, "participants_count", None)})
            try:
                res = await client(functions.messages.GetFullChatRequest(chat_id=e.id))
                fc = getattr(res, "full_chat", None)
                if fc is not None:
                    out["bio"] = getattr(fc, "about", None)
                    parts = getattr(fc, "participants", None)
                    if parts is not None:
                        out["members"] = len(getattr(parts, "participants", None) or [])
            except Exception:
                pass
        if out["username"]:
            out["link"] = "https://t.me/" + out["username"]
        elif out["type"] in ("group", "channel"):
            out["link"] = None
    except Exception as ex:
        log.warning("profile serialize failed: %s", ex)
    return out


# ---- bot inline button callback ---------------------------------------------
@router.post("/callback")
async def api_callback(body: CallbackBody):
    client = await require_client()
    entity = await resolve_entity(client, body.chat_id)
    data = None
    try:
        data = base64.b64decode(body.data or "") or None
    except Exception:
        data = None
    try:
        res = await client(functions.messages.GetBotCallbackAnswerRequest(
            peer=entity, msg_id=body.msg_id, data=data))
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    # the bot may have edited its message as a reaction to the button
    msg = None
    try:
        fetched = await client.get_messages(entity, ids=[body.msg_id])
        if fetched and fetched[0] is not None:
            ser = await serialize_messages(client, entity, [fetched[0]], body.chat_id)
            msg = ser[0] if ser else None
    except Exception:
        pass
    return {"ok": True, "answer": getattr(res, "message", None) or "",
            "alert": bool(getattr(res, "alert", False)),
            "url": getattr(res, "url", None), "message": msg}


# ---- global search (all chats + messages everywhere) -------------------------
@router.get("/search_global")
async def api_search_global(q: str = Query(..., min_length=1),
                            limit: int = Query(20, ge=1, le=50)):
    client = await require_client()
    q = q.strip()
    chats = []
    try:
        async for d in client.iter_dialogs(search=q, limit=10):
            dj = dialog_to_json(d)
            if dj:
                chats.append(dj)
    except Exception as e:
        log.warning("global dialog search failed: %s", e)
    messages = []
    try:
        res = await client(functions.messages.SearchGlobalRequest(
            q=q, filter=types.InputMessagesFilterEmpty(),
            min_date=None, max_date=None, offset_rate=0,
            offset_peer=types.InputPeerEmpty(), offset_id=0, limit=limit))
        emap = {}
        for c in (getattr(res, "chats", None) or []):
            emap[get_peer_id(c)] = c
        for u in (getattr(res, "users", None) or []):
            emap[get_peer_id(u)] = u
        for m in (getattr(res, "messages", None) or []):
            mj = message_to_json(m)
            if not mj:
                continue
            pid = m.chat_id if m.chat_id is not None else None
            ent = emap.get(pid) if pid is not None else None
            etype = "chat"
            if isinstance(ent, types.User):
                etype = "bot" if getattr(ent, "bot", False) else "user"
            elif isinstance(ent, types.Channel):
                etype = "channel" if ent.broadcast else "group"
            elif isinstance(ent, types.Chat):
                etype = "group"
            mj["chat"] = {"id": pid,
                          "name": display_name(ent) if ent is not None else None,
                          "type": etype,
                          "has_photo": getattr(ent, "photo", None) is not None if ent is not None else False}
            messages.append(mj)
    except Exception as e:
        log.warning("global message search failed: %s", e)
    return {"chats": chats, "messages": messages}
