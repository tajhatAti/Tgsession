"""Single Telegram client state: login, session file persistence, helpers."""
import asyncio
import os
from typing import Dict, Optional

from fastapi import HTTPException
from telethon import TelegramClient
from telethon import errors as tg_errors
from telethon.errors import FloodWaitError

from .config import SESSION_FILE, log
from .serialize import display_name

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
        from .updates import register_update_handlers  # lazy (avoid import cycle)
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
