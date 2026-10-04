"""Media endpoints: avatars, message thumbnails/photos, Range-streamed files."""
from fastapi import APIRouter, Depends, HTTPException, Request
from telethon import types

from ..config import log
from ..media import _bytes_response, _sniff_image, get_full_photo, get_thumb, stream_media
from ..security import require_site
from ..tgstate import require_client, resolve_entity
from ..util import avatar_cache, dl_semaphore, listify

router = APIRouter(prefix="/api", dependencies=[Depends(require_site)])


@router.get("/avatar/{chat_id}")
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


@router.get("/media/{chat_id}/{msg_id}")
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
