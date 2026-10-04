"""Incoming Telegram updates (typing indicators) kept in memory."""
import time
from typing import List

from telethon import events, types
from telethon.utils import get_peer_id

from .config import log
from .serialize import display_name
from .tgstate import state

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
