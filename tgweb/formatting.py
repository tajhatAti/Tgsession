"""Telegram message entities -> safe HTML (bold/italic/links/code/spoiler).
Own implementation (telethon's html.unparse renders no spoilers)."""
from typing import Optional, Tuple

from telethon import types
from telethon.helpers import add_surrogate, del_surrogate, within_surrogate

from .config import log
from .util import _ESC

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
