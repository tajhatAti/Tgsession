"""Auth endpoints: site password, session import (the only Telegram login),
status, logout. Phone/OTP login intentionally NOT offered."""
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from telethon import TelegramClient
from telethon import errors as tg_errors
from telethon.sessions import StringSession

from ..config import API_HASH, API_ID, AUTH_COOKIE, AUTH_KEY, AUTH_TTL, PASSWORD, log
from ..schemas import PasswordBody, SessionBody
from ..security import cookie_ok, make_cookie_value, require_site, secure_cookie
from ..tgstate import _remove_session_file, _safe_disconnect, state

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
