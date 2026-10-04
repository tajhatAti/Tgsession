"""Media pipeline: avatars/thumbnails (cached, bounded), photos, and
HTTP Range streaming (206 partial content) for videos/audio/files."""
import re
from typing import Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from telethon import types

from .config import log
from .serialize import media_info, webpage_json
from .util import dl_semaphore, photo_cache, thumb_cache

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
