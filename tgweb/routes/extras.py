"""Extra endpoints: stories, inline @bot queries, scheduled messages,
own profile photo, message links, username resolve/join."""
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from telethon import functions, types, utils as tl_utils

from ..config import MAX_UPLOAD, log
from ..media import _bytes_response, _sniff_image
from ..security import require_site
from ..serialize import display_name, media_info
from ..tgstate import require_client, resolve_entity
from ..util import avatar_cache, dl_semaphore

router = APIRouter(prefix="/api", dependencies=[Depends(require_site)])


# ---------------------------------------------------------------- helpers
def _ts(dt) -> int:
    try:
        return int(dt.timestamp())
    except Exception:
        return 0


# server-side cache of StoryItem objects so /api/story_media can stream them
story_cache: dict = {}


def _story_cache_put(key, story):
    story_cache[key] = story
    if len(story_cache) > 400:
        for k in list(story_cache.keys())[:200]:
            story_cache.pop(k, None)


def _story_media_json(story):
    """media info dict for a StoryItem (media_info expects a .media attr)."""
    class _Shim:
        pass
    shim = _Shim()
    shim.media = getattr(story, "media", None)
    return media_info(shim)


def story_to_json(story, chat_id: int) -> dict:
    mid = int(getattr(story, "id", 0) or 0)
    _story_cache_put((chat_id, mid), story)
    views = getattr(story, "views", None)
    if views is not None and not isinstance(views, int):
        views = getattr(views, "views_count", None)
    return {
        "id": mid,
        "chat_id": chat_id,
        "date": _ts(getattr(story, "date", None)),
        "expire_date": _ts(getattr(story, "expire_date", None)),
        "caption": getattr(story, "caption", None) or "",
        "views": views,
        "out": bool(getattr(story, "out", False)),
        "media": _story_media_json(story),
        "thumb_url": "api/story_media/%d/%d?kind=thumb" % (chat_id, mid),
        "media_url": "api/story_media/%d/%d?kind=full" % (chat_id, mid),
    }


def _alive(story) -> bool:
    if getattr(story, "min", False):
        return False
    exp = _ts(getattr(story, "expire_date", None))
    return exp == 0 or exp > int(time.time())


async def _peer_stories(client, entity, chat_id: int) -> dict:
    res = await client(functions.stories.GetPeerStoriesRequest(peer=entity))
    ps = res.stories[0] if getattr(res, "stories", None) else None
    items = [s for s in (getattr(ps, "stories", None) or []) if _alive(s)]
    max_read = int(getattr(ps, "max_read_id", 0) or 0)
    stories = [story_to_json(s, chat_id) for s in items]
    return {
        "chat_id": chat_id,
        "max_read_id": max_read,
        "unseen": len([s for s in stories if s["id"] > max_read]),
        "stories": stories,
    }


# ---------------------------------------------------------------- stories
@router.get("/stories")
async def api_stories(chat_id: int = 0):
    """Stories bar data: every peer with active stories (contacts) + self."""
    client = await require_client()
    out = {"peers": [], "me": None}
    me_id = None
    try:
        me_ent = await client.get_me()
        me_id = int(getattr(me_ent, "id", 0) or 0)
        mine = await _peer_stories(client, types.InputPeerSelf(), me_id)
        if mine["stories"]:
            mine["name"] = display_name(me_ent) or "My story"
            mine["has_photo"] = getattr(me_ent, "photo", None) is not None
            out["me"] = mine
    except Exception as e:
        log.warning("self stories failed: %s", e)
    if chat_id:
        try:
            entity = await resolve_entity(client, chat_id)
            out["peers"].append(await _peer_stories(client, entity, chat_id))
        except HTTPException:
            raise
        except Exception as e:
            log.warning("peer stories failed: %s", e)
        return out
    try:
        res = await client(functions.stories.GetAllStoriesRequest())
        peer_stories = list(getattr(res, "peer_stories", None) or [])
    except Exception as e:
        log.warning("GetAllStories failed: %s", e)
        peer_stories = []
    for ps in peer_stories:
        try:
            peer = getattr(ps, "peer", None)
            if peer is None:
                continue
            chat_id2 = tl_utils.get_peer_id(peer)
            if chat_id2 == me_id:
                continue
            await resolve_entity(client, chat_id2)
            try:
                entity = await client.get_entity(chat_id2)
            except Exception:
                entity = peer
            data = {
                "chat_id": chat_id2,
                "max_read_id": int(getattr(ps, "max_read_id", 0) or 0),
                "stories": [story_to_json(s, chat_id2)
                            for s in (getattr(ps, "stories", None) or []) if _alive(s)],
            }
            data["unseen"] = len([s for s in data["stories"] if s["id"] > data["max_read_id"]])
            data["name"] = display_name(entity)
            data["username"] = getattr(entity, "username", None)
            data["has_photo"] = getattr(entity, "photo", None) is not None
            if data["stories"]:
                out["peers"].append(data)
        except Exception as e:
            log.warning("story peer failed: %s", e)
    return out


@router.get("/story_media/{chat_id}/{story_id}")
async def api_story_media(chat_id: int, story_id: int, kind: str = "thumb"):
    client = await require_client()
    story = story_cache.get((chat_id, story_id))
    if story is None:
        try:
            entity = await resolve_entity(client, chat_id)
            res = await client(functions.stories.GetPeerStoriesRequest(peer=entity))
            ps = res.stories[0] if getattr(res, "stories", None) else None
            for s in (getattr(ps, "stories", None) or []):
                if int(getattr(s, "id", 0) or 0) == story_id:
                    story = s
                    _story_cache_put((chat_id, story_id), s)
                    break
        except Exception as e:
            log.warning("story refetch failed: %s", e)
    if story is None:
        raise HTTPException(404, "Story not found")
    if kind == "thumb":
        async with dl_semaphore:
            try:
                data = await client.download_media(story, thumb=-1, file=bytes)
            except Exception as e:
                log.warning("story thumb failed: %s", e)
                data = None
        if not data:
            raise HTTPException(404, "No thumbnail")
        data = bytes(data)
        return _bytes_response((data, _sniff_image(data)), "private, max-age=604800")
    if kind == "full":
        media = getattr(story, "media", None)
        doc = getattr(media, "document", None)
        async with dl_semaphore:
            try:
                data = await client.download_media(story, file=bytes)
            except Exception as e:
                log.warning("story media failed: %s", e)
                data = None
        if not data:
            raise HTTPException(404, "Could not load story")
        data = bytes(data)
        if doc is not None:
            return _bytes_response((data, getattr(doc, "mime_type", None) or "video/mp4"),
                                   "private, max-age=604800")
        return _bytes_response((data, _sniff_image(data)), "private, max-age=604800")
    raise HTTPException(400, "Unknown kind")


@router.post("/stories/read")
async def api_stories_read(body: dict):
    client = await require_client()
    entity = await resolve_entity(client, int(body.get("chat_id") or 0))
    try:
        await client(functions.stories.ReadStoriesRequest(
            peer=entity, max_id=int(body.get("max_id") or 0)))
    except Exception as e:
        log.warning("ReadStories failed: %s", e)
    return {"ok": True}


@router.post("/stories/delete")
async def api_stories_delete(body: dict):
    client = await require_client()
    entity = await resolve_entity(client, int(body.get("chat_id") or 0))
    try:
        await client(functions.stories.DeleteStoriesRequest(
            peer=entity, id=[int(body.get("story_id") or 0)]))
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@router.post("/stories/upload")
async def api_stories_upload(file: UploadFile = File(...), caption: str = Form("")):
    client = await require_client()
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 2 GB)")
    if _sniff_image(data).startswith("image/"):
        uploaded = await client.upload_file(data)
        media = types.InputMediaUploadedPhoto(file=uploaded)
    else:
        uploaded = await client.upload_file(data, file_name=file.filename or "story")
        media = types.InputMediaUploadedDocument(
            file=uploaded, mime_type=file.content_type or "video/mp4",
            attributes=[types.DocumentAttributeVideo(
                duration=0, w=0, h=0, supports_streaming=True)])
    try:
        await client(functions.stories.SendStoryRequest(
            peer=types.InputPeerSelf(), media=media,
            privacy_rules=[types.InputPrivacyValueAllowAll()], caption=caption or None))
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


# ---------------------------------------------------------------- inline bots
# cached inline query results: token -> (expires, query_id, results)
_inline_cache: dict = {}
_INLINE_TTL = 120


@router.get("/inline_query")
async def api_inline_query(chat_id: int, bot: str = Query(..., min_length=2),
                           q: str = "", offset: str = ""):
    client = await require_client()
    bot = bot.lstrip("@")
    try:
        bot_ent = await client.get_entity(bot)
    except Exception:
        raise HTTPException(404, "Bot @%s not found" % bot)
    peer = await resolve_entity(client, chat_id)
    try:
        res = await client(functions.messages.GetInlineBotResultsRequest(
            bot=bot_ent, peer=peer, query=q, offset=offset))
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    raw_results = list(getattr(res, "results", None) or [])
    query_id = int(getattr(res, "query_id", 0) or 0)
    results = []
    for i, r in enumerate(raw_results[:50]):
        msg = getattr(r, "send_message", None)
        results.append({
            "i": i,
            "id": getattr(r, "id", None),
            "type": getattr(r, "type", "") or "article",
            "title": getattr(msg, "title", None) or getattr(r, "title", None) or "",
            "description": getattr(msg, "description", None) or getattr(r, "description", None) or "",
            "url": getattr(r, "url", None),
            "has_thumb": bool(getattr(r, "thumb", None) or getattr(r, "photo", None)),
        })
    token = uuid.uuid4().hex
    _inline_cache[token] = (time.time() + _INLINE_TTL, query_id, raw_results)
    if len(_inline_cache) > 30:
        for k in [k for k, v in _inline_cache.items() if v[0] < time.time()][:20]:
            _inline_cache.pop(k, None)
    return {"token": token, "query_id": query_id,
            "next_offset": getattr(res, "next_offset", "") or "", "results": results}


@router.get("/inline_media/{token}/{idx}")
async def api_inline_media(token: str, idx: int):
    client = await require_client()
    entry = _inline_cache.get(token)
    if not entry or entry[0] < time.time():
        raise HTTPException(404, "Query expired — type again")
    results = entry[2]
    if idx < 0 or idx >= len(results):
        raise HTTPException(404, "No such result")
    r = results[idx]
    try:
        thumb_doc = getattr(r, "thumb", None) or getattr(r, "photo", None)
        if thumb_doc is None:
            raise HTTPException(404, "No thumbnail")
        async with dl_semaphore:
            data = await client.download_media(thumb_doc, file=bytes, thumb=-1)
        if not data:
            raise HTTPException(404, "No thumbnail")
        return _bytes_response((bytes(data), _sniff_image(data)), "private, max-age=600")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(404, "No thumbnail: %s" % e)


@router.post("/send_inline")
async def api_send_inline(body: dict):
    client = await require_client()
    chat_id = int(body.get("chat_id") or 0)
    token = body.get("token") or ""
    result_id = body.get("result_id") or ""
    reply_to = body.get("reply_to") or None
    entry = _inline_cache.get(token)
    if not entry or entry[0] < time.time():
        raise HTTPException(400, "Query expired — pick again")
    query_id = entry[1]
    peer = await resolve_entity(client, chat_id)
    try:
        await client(functions.messages.SendInlineBotResultRequest(
            peer=peer, query_id=query_id, id=result_id,
            reply_to=types.InputReplyToMessage(reply_to_msg_id=reply_to) if reply_to else None,
            random_id=int(uuid.uuid4().int & 0x7FFFFFFF)))
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


# ---------------------------------------------------------------- scheduled
@router.get("/scheduled")
async def api_scheduled(chat_id: int):
    client = await require_client()
    entity = await resolve_entity(client, chat_id)
    from ..serialize import serialize_messages
    try:
        res = await client(functions.messages.GetScheduledHistoryRequest(peer=entity, hash=0))
        msgs = list(getattr(res, "messages", None) or [])
        data = await serialize_messages(client, entity, msgs, chat_id)
        for d in data:
            d["scheduled"] = True
        data.sort(key=lambda x: x["date"] or "")
        return {"messages": data}
    except Exception as e:
        log.warning("GetScheduledHistory failed: %s", e)
        return {"messages": []}


@router.post("/schedule")
async def api_schedule(body: dict):
    client = await require_client()
    chat_id = int(body.get("chat_id") or 0)
    text = (body.get("text") or "").strip()
    when = int(body.get("schedule_date") or 0)
    reply_to = body.get("reply_to") or None
    if not text:
        raise HTTPException(400, "Empty message")
    if when <= 0:
        raise HTTPException(400, "schedule_date must be a unix timestamp")
    entity = await resolve_entity(client, chat_id)
    try:
        await client.send_message(entity, text, schedule_date=when, reply_to=reply_to)
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


@router.post("/scheduled/delete")
async def api_scheduled_delete(body: dict):
    client = await require_client()
    chat_id = int(body.get("chat_id") or 0)
    msg_id = int(body.get("msg_id") or 0)
    entity = await resolve_entity(client, chat_id)
    try:
        await client(functions.messages.DeleteScheduledMessagesRequest(peer=entity, id=[msg_id]))
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    return {"ok": True}


# ---------------------------------------------------------------- misc extras
@router.post("/tg/me/photo")
async def api_me_photo(file: UploadFile = File(...)):
    client = await require_client()
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if not _sniff_image(data).startswith("image/"):
        raise HTTPException(400, "Only image files are supported")
    try:
        uploaded = await client.upload_file(data)
        await client(functions.photos.UploadProfilePhotoRequest(file=uploaded))
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    avatar_cache._d.clear()
    avatar_cache._bytes = 0
    return {"ok": True}


@router.post("/link")
async def api_link(body: dict):
    """Export a t.me link for a message (channels/supergroups/public users)."""
    client = await require_client()
    chat_id = int(body.get("chat_id") or 0)
    msg_id = int(body.get("msg_id") or 0)
    entity = await resolve_entity(client, chat_id)
    try:
        full = await client.get_entity(chat_id)
    except Exception:
        full = entity
    try:
        if isinstance(full, (types.Channel, types.Chat)):
            try:
                res = await client(functions.channels.ExportMessageLinkRequest(
                    channel=full, id=msg_id))
                link = getattr(res, "link", None) or ""
                if link:
                    return {"ok": True, "link": link}
            except Exception:
                pass
        username = getattr(full, "username", None)
        if username:
            return {"ok": True, "link": "https://t.me/%s/%d" % (username, msg_id)}
        raise HTTPException(400, "This chat has no public link")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)


@router.get("/resolve")
async def api_resolve(username: str = Query(..., min_length=2)):
    """Resolve @username / t.me link to chat info (for starting new chats)."""
    client = await require_client()
    username = username.strip().rstrip("/").split("/")[-1].lstrip("@")
    try:
        ent = await client.get_entity(username)
    except Exception:
        raise HTTPException(404, "Nothing found for @%s" % username)
    cid = tl_utils.get_peer_id(ent)
    is_channel = isinstance(ent, types.Channel)
    can_join = bool(is_channel and (getattr(ent, "broadcast", False) or getattr(ent, "megagroup", False)))
    return {
        "ok": True,
        "chat": {
            "id": cid,
            "type": "channel" if getattr(ent, "broadcast", False) else (
                "group" if (is_channel or isinstance(ent, types.Chat)) else "user"),
            "name": display_name(ent) or username,
            "username": getattr(ent, "username", None),
            "has_photo": getattr(ent, "photo", None) is not None,
            "verified": bool(getattr(ent, "verified", False)),
            "participants_count": getattr(ent, "participants_count", None),
        },
        "can_join": can_join,
    }


@router.post("/join")
async def api_join(body: dict):
    client = await require_client()
    username = (body.get("username") or "").strip().rstrip("/").split("/")[-1].lstrip("@")
    try:
        ent = await client.get_entity(username)
        if isinstance(ent, types.Channel):
            await client(functions.channels.JoinChannelRequest(channel=ent))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, "Telegram error: %s" % e)
