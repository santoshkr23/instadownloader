"""Optional password gate for the whole site, for while it isn't public yet.

Set SITE_PASSWORD (and optionally SITE_USER, default "owner") and every page asks
the browser for a username and password first. /health stays open so Docker can
check the container. Pages also get "noindex" so search engines skip them.
Remove SITE_PASSWORD from .env when you launch.
"""
import base64
import hmac
import os
import time
from collections import defaultdict, deque

SITE_USER = os.getenv("SITE_USER", "owner")
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "")
OPEN_PATHS = ("/health",)

_fails: dict[str, deque] = defaultdict(deque)


class PasswordGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not SITE_PASSWORD or scope["path"] in OPEN_PATHS:
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        ip = (headers.get(b"x-forwarded-for", b"").decode().split(",")[0].strip()
              or (scope.get("client") or ("?",))[0])
        q = _fails[ip]
        now = time.time()
        while q and now - q[0] > 600:
            q.popleft()

        if len(q) < 10 and _ok(headers.get(b"authorization", b"").decode()):
            async def send_noindex(message):
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append((b"x-robots-tag", b"noindex, nofollow"))
                await send(message)
            return await self.app(scope, receive, send_noindex)

        if headers.get(b"authorization"):
            q.append(now)
        body = b"Private site: sign in to continue." if len(q) < 10 else b"Too many wrong passwords. Try again in 10 minutes."
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"www-authenticate", b'Basic realm="Private site", charset="UTF-8"'),
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"x-robots-tag", b"noindex, nofollow"),
        ]})
        await send({"type": "http.response.body", "body": body})


def _ok(header: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    return hmac.compare_digest(user.encode(), SITE_USER.encode()) & hmac.compare_digest(pw.encode(), SITE_PASSWORD.encode())
