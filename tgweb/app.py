"""FastAPI application assembly: middleware, routers, static files, errors."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send
from telethon import errors as tg_errors
from telethon.errors import FloodWaitError

from .client import init_telegram
from .config import STATIC_DIR, log
from .routes import auth as routes_auth
from .routes import chat as routes_chat
from .routes import media as routes_media
from .routes import extras as routes_extras
from .tgstate import state


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(init_telegram())
    try:
        yield
    finally:
        task.cancel()
        if state.client is not None:
            try:
                await asyncio.wait_for(state.client.disconnect(), timeout=5)
            except Exception:
                pass


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class PrefixStripMiddleware:
    """The app may be reverse-proxied under an unknown path prefix such as
    /live/<slug>/. The page computes its own <base href>, so requests arrive
    with the full prefixed path; strip everything before the known route."""

    MARKERS = ("/api/", "/css/", "/js/", "/icons/", "/healthz", "/manifest.webmanifest")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path") or "/"
            if not self._known(path):
                new_path = self._strip(path)
                if new_path and new_path != path:
                    scope["path"] = new_path
                    try:
                        scope["raw_path"] = new_path.encode()
                    except Exception:
                        pass
        await self.app(scope, receive, send)

    @classmethod
    def _known(cls, path: str) -> bool:
        if path == "/":
            return True
        return any(path == m or path.startswith(m) for m in cls.MARKERS)

    @classmethod
    def _strip(cls, path: str):
        best = -1
        for m in cls.MARKERS:
            i = path.rfind(m)
            if i > best:
                best = i
        if best > 0:
            return path[best:]
        last = path.rsplit("/", 1)[-1]
        if path.endswith("/") or "." not in last:
            return "/"          # any page path -> SPA index
        return None


app.add_middleware(PrefixStripMiddleware)


class AppStaticFiles(StaticFiles):
    """Static files with light caching (always revalidate — easy customizing)."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


app.include_router(routes_auth.public)
app.include_router(routes_auth.router)
app.include_router(routes_chat.router)
app.include_router(routes_media.router)
app.include_router(routes_extras.router)

# static frontend at the root: / -> index.html, /css/* /js/* /icons/* ...
app.mount("/", AppStaticFiles(directory=STATIC_DIR, html=True), name="static")


# ---------------------------------------------------------------------------
# error handlers — JSON only, never a stack trace to the client
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "Invalid request parameters"}, status_code=422)


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    if isinstance(exc, FloodWaitError):
        secs = int(getattr(exc, "seconds", 0) or 0)
        return JSONResponse({"detail": "Telegram flood limit — retry in %ss" % secs},
                            status_code=429, headers={"Retry-After": str(secs or 1)})
    if isinstance(exc, tg_errors.RPCError):
        log.warning("telegram rpc error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse({"detail": "Telegram error: %s" % exc}, status_code=502)
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)
