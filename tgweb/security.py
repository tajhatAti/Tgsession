"""Site auth: HMAC-signed httpOnly cookie (no sessions, no database)."""
import hashlib
import hmac
import time

from fastapi import HTTPException, Request

from .config import AUTH_COOKIE, AUTH_KEY, AUTH_TTL

def make_cookie_value() -> str:
    exp = int(time.time()) + AUTH_TTL
    sig = hmac.new(AUTH_KEY, str(exp).encode(), hashlib.sha256).hexdigest()
    return "%d.%s" % (exp, sig)


def cookie_ok(request: Request) -> bool:
    raw = request.cookies.get(AUTH_COOKIE)
    if not raw or "." not in raw:
        return False
    exp_s, _, sig = raw.rpartition(".")
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    good = hmac.new(AUTH_KEY, exp_s.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    return exp > time.time()


def secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


async def require_site(request: Request) -> None:
    if not cookie_ok(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
