"""Auth endpoints: site password, session import (the only Telegram login),
status, logout. Phone/OTP login intentionally NOT offered."""
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from telethon import TelegramClient
from telethon import errors as tg_errors
from telethon.sessions import StringSession

import re

from ..config import API_HASH, API_ID, AUTH_COOKIE, AUTH_KEY, AUTH_TTL, PASSWORD, log
from ..schemas import (CodeBody, PasswordBody, PhoneBody, ProfileEditBody,
                       SessionBody)
from telethon import functions

from ..security import cookie_ok, make_cookie_value, require_site, secure_cookie
from ..serialize import display_name
from ..tgstate import _remove_session_file, _safe_disconnect, require_client, state

# unauthenticated endpoints (site login itself + health check)
public = APIRouter()

@public.post("/api/login")
async def api_login(body: PasswordBody, request: Request):
    got = hmac.new(AUTH_KEY, (body.password or "").encode("utf-8"), hashlib.sha256).digest()
    want = hmac.new(AUTH_KEY, PASSWORD.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(got, want):
        raise HTTPException(status_code=401, detail="Wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(AUTH_COOKIE, make_cookie_value(), max_age=AUTH_TTL, httponly=True,
                    samesite="lax", secure=secure_cookie(request), path="/")
    return resp


@public.get("/healthz")
async def healthz():
    return {"ok": True}


# everything below requires the site cookie
router = APIRouter(prefix="/api", dependencies=[Depends(require_site)])


@router.get("/status")
async def api_status():
    return {"tg": state.authorized, "me": state.me, "error": state.last_error}


@router.post("/site_logout")
async def api_site_logout(request: Request):
    if not cookie_ok(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp

@router.post("/tg/login_phone")
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


@router.post("/tg/login_code")
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


@router.post("/tg/login_password")
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


@router.post("/tg/import_session")
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

@router.post("/tg/logout")
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


# ---- edit own profile (Settings) -------------------------------------------
@router.post("/tg/me/edit")
async def api_me_edit(body: ProfileEditBody):
    client = await require_client()
    try:
        me = await client(functions.account.UpdateProfileRequest(
            first_name=(body.first_name if body.first_name is not None else None),
            last_name=(body.last_name if body.last_name is not None else None),
            about=(body.about if body.about is not None else None)))
    except tg_errors.RPCError as e:
        raise HTTPException(400, "Telegram error: %s" % e)
    try:
        if me is not None:
            state.me = {"id": getattr(me, "id", None), "name": display_name(me),
                        "username": getattr(me, "username", None)}
            state.me_id = getattr(me, "id", None)
    except Exception:
        pass
    return {"ok": True, "me": state.me}
