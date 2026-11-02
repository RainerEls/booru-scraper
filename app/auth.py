import hmac
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketClose

from app import config

COOKIE = "booru_scraper_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 5 * 60
MAX_FORM_BYTES = 4096
OPEN_PATHS = ("/login", "/health")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

sessions = {}
failures = {}


def enabled():
    return bool(config.APP_PASSWORD)


def logged_in(connection):
    return sessions.get(connection.cookies.get(COOKIE), 0) > time.monotonic()


def password_matches(password):
    return hmac.compare_digest(password.encode(), config.APP_PASSWORD.encode())


def start_session(response):
    now = time.monotonic()
    for token in [token for token, expires in sessions.items() if expires <= now]:
        del sessions[token]
    token = secrets.token_urlsafe(32)
    sessions[token] = now + SESSION_SECONDS
    response.set_cookie(
        COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax", secure=config.COOKIE_SECURE
    )


def end_session(request, response):
    sessions.pop(request.cookies.get(COOKIE), None)
    response.delete_cookie(COOKIE)


def locked_out(client):
    count, last_failure = failures.get(client, (0, 0))
    return count >= MAX_FAILURES and time.monotonic() - last_failure < LOCKOUT_SECONDS


def record_failure(client):
    now = time.monotonic()
    for key in [key for key, (_, last) in failures.items() if now - last >= LOCKOUT_SECONDS]:
        del failures[key]
    failures[client] = (failures.get(client, (0, 0))[0] + 1, now)


def clear_failures(client):
    failures.pop(client, None)


async def submitted_password(request):
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail="Form too large")
    return parse_qs(body.decode(errors="replace")).get("password", [""])[0]


def same_origin(connection):
    origin = connection.headers.get("origin")
    return origin is None or urlsplit(origin).netloc == connection.headers.get("host")


def is_open(path):
    return path in OPEN_PATHS or path.startswith("/static/")


def refusal(connection, method):
    if method not in SAFE_METHODS and not same_origin(connection):
        return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    if is_open(connection.url.path) or logged_in(connection):
        return None
    if "text/html" in connection.headers.get("accept", ""):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "Login required"}, status_code=401)


class RequireLogin:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan" or not enabled():
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        if scope["type"] == "websocket":
            allowed = logged_in(connection) and same_origin(connection)
            response = None if allowed else WebSocketClose()
        else:
            response = refusal(connection, scope["method"])
        if response is None:
            await self.app(scope, receive, send)
        else:
            await response(scope, receive, send)
