"""YouTube/Instagram login cookies and the download proxy, set in the admin panel.

Kept in data/secrets/ on the server only: never committed to GitHub (unlike
data/config.json), because a cookies file is a login and a proxy URL usually has a
password in it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from urllib.parse import urlparse

from . import config

SECRETS_DIR = os.path.join(config.ROOT, "data", "secrets")
COOKIES_PATH = os.path.join(SECRETS_DIR, "cookies.txt")
PROXY_PATH = os.path.join(SECRETS_DIR, "proxy.json")
# a cookies.txt mounted by Docker/Render (older setup) is still used if no admin upload exists
FALLBACK_COOKIES = os.getenv("COOKIES_FILE") or "/etc/secrets/cookies.txt"
PROXY_SCOPES = {"youtube": "YouTube only", "all": "All platforms"}
MAX_COOKIES_BYTES = 512 * 1024

_lock = threading.Lock()


class SecretError(ValueError):
    pass


def _ensure_dir():
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)


# ---------------------------------------------------------------- cookies
def cookies_path() -> str | None:
    if os.path.exists(COOKIES_PATH):
        return COOKIES_PATH
    if os.path.exists(FALLBACK_COOKIES):
        return FALLBACK_COOKIES
    return None


def save_cookies(text: str) -> dict:
    text = (text or "").replace("\r\n", "\n").strip() + "\n"
    if len(text.encode()) > MAX_COOKIES_BYTES:
        raise SecretError("That cookies file is too big (max 512 KB).")
    rows = [l for l in text.splitlines() if l.strip() and not l.startswith("#") or l.startswith("#HttpOnly_")]
    good = [l for l in rows if len(l.split("\t")) >= 7]
    if not good:
        raise SecretError("That doesn't look like a cookies.txt file. Export it with the "
                          "'Get cookies.txt LOCALLY' extension; the first line should be '# Netscape HTTP Cookie File'.")
    if not text.startswith("# Netscape") and not text.startswith("# HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    _ensure_dir()
    with _lock:
        tmp = COOKIES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, COOKIES_PATH)
    return cookies_info()


def delete_cookies() -> None:
    with _lock:
        try:
            os.remove(COOKIES_PATH)
        except FileNotFoundError:
            pass


def cookies_info() -> dict:
    path = cookies_path()
    if not path:
        return {"present": False}
    domains: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 7 and (not line.startswith("#") or line.startswith("#HttpOnly_")):
                    d = parts[0].replace("#HttpOnly_", "").lstrip(".")
                    base = ".".join(d.split(".")[-2:])
                    domains[base] = domains.get(base, 0) + 1
    except OSError:
        pass
    return {
        "present": True,
        "from_admin": path == COOKIES_PATH,
        "updated": time.strftime("%d %b %Y, %H:%M", time.localtime(os.path.getmtime(path))),
        "sites": sorted(domains, key=lambda d: -domains[d])[:6],
        "has_youtube": any(d in ("youtube.com", "google.com") for d in domains),
    }


# ---------------------------------------------------------------- proxy
def get_proxy() -> dict:
    try:
        with open(PROXY_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return {"url": data.get("url", ""), "scope": data.get("scope", "youtube")}
    except (OSError, ValueError):
        return {"url": "", "scope": "youtube"}


def save_proxy(url: str, scope: str) -> None:
    url = (url or "").strip()
    if scope not in PROXY_SCOPES:
        scope = "youtube"
    if url:
        p = urlparse(url)
        if p.scheme not in ("http", "https", "socks5", "socks5h") or not p.hostname:
            raise SecretError("Proxy must look like http://user:password@host:port or socks5://user:password@host:port")
    _ensure_dir()
    with _lock:
        with open(PROXY_PATH, "w", encoding="utf-8") as f:
            json.dump({"url": url, "scope": scope}, f)
        os.chmod(PROXY_PATH, 0o600)


def proxy_for(url: str) -> str | None:
    p = get_proxy()
    if not p["url"]:
        return None
    if p["scope"] == "all":
        return p["url"]
    host = (urlparse(url).hostname or "").lower()
    return p["url"] if re.search(r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$", host) else None


def masked_proxy() -> str:
    """Proxy URL with the password hidden, for showing in the admin panel."""
    url = get_proxy()["url"]
    if not url:
        return ""
    p = urlparse(url)
    if p.password:
        return url.replace(f":{p.password}@", ":••••••@", 1)
    return url
