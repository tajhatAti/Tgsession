"""Startup auto-login from the saved session file — never crashes the app."""
import asyncio
import os

from telethon import TelegramClient
from telethon import errors as tg_errors
from telethon.sessions import StringSession

from .config import API_HASH, API_ID, SESSION_FILE, log
from .tgstate import _remove_session_file, _safe_disconnect, state

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
