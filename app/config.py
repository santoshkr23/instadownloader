"""Site settings (platforms, affiliate offers, site name) and offer click stats.

Settings live in data/config.json in the GitHub repo, so they survive Render's
free plan wiping its disk:

* The server reads them from GitHub at startup (falls back to the local file).
* Saving in the admin panel applies them immediately on the server and commits
  the file to GitHub, which also rebuilds the GitHub Pages copy of the site.
* Click counts are kept in memory and written to data/stats.json every few
  minutes (Render and the Pages workflow ignore that file, so it doesn't
  trigger rebuilds).

Needs GITHUB_TOKEN (a fine-grained token with "Contents: read and write" on this
repo). Without it, changes still apply but are lost when the server restarts.
This module has no web-framework imports so build_static.py can use it too.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger("config")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "data", "config.json")
STATS_PATH = os.path.join(ROOT, "data", "stats.json")
CONFIG_REPO_PATH = "data/config.json"
STATS_REPO_PATH = "data/stats.json"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "santoshkr23/instadownloader").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
STATS_FLUSH_SECONDS = int(os.getenv("STATS_FLUSH_SECONDS", "300"))

MODES = ("download", "offers")
PLACEMENTS = {
    "home": "Home page",
    "pages": "Every downloader page",
    "after_download": "Below download buttons",
    "blocked": "Platforms in offers mode (e.g. YouTube)",
}

_lock = threading.RLock()
_config: dict | None = None
_last_sync: dict = {"ok": None, "message": "", "at": None}


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- GitHub I/O
def github_enabled() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def _gh(method: str, path: str, body: dict | None = None) -> dict:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={GITHUB_BRANCH}"
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "instadownloader-admin")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _gh_read(path: str) -> tuple[str, str]:
    data = _gh("GET", path)
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def _gh_write(path: str, text: str, message: str) -> None:
    for attempt in range(2):
        try:
            try:
                sha = _gh("GET", path)["sha"]
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                sha = None
            body = {"message": message, "content": base64.b64encode(text.encode()).decode(), "branch": GITHUB_BRANCH}
            if sha:
                body["sha"] = sha
            _gh("PUT", path, body)
            return
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt == 0:  # someone else wrote meanwhile; retry with the new sha
                continue
            raise


# ---------------------------------------------------------------- validation
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
_HOST_RE = re.compile(r"^(?=.{3,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_RESERVED_SLUGS = {"admin", "api", "static", "health", "go", "robots.txt", "sitemap.xml"}


def _text(v, limit: int, field: str, required=False) -> str:
    s = str(v or "").strip()
    if required and not s:
        raise ConfigError(f"{field} is required.")
    if len(s) > limit:
        raise ConfigError(f"{field} is too long (max {limit} characters).")
    return s


def _url(v, field: str, required=False) -> str:
    s = _text(v, 2000, field, required)
    if s:
        p = urlparse(s)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ConfigError(f"{field} must start with https://")
    return s


def clean_platform(p: dict) -> dict:
    key = _text(p.get("key"), 31, "Key", True).lower()
    if not _KEY_RE.match(key):
        raise ConfigError("Key may only use lowercase letters, numbers and dashes (e.g. tiktok).")
    slug = _text(p.get("slug") or f"{key}-downloader", 61, "Page address").lower()
    if not _SLUG_RE.match(slug) or slug in _RESERVED_SLUGS:
        raise ConfigError("Page address may only use lowercase letters, numbers and dashes.")
    hosts_in = p.get("hosts") or []
    if isinstance(hosts_in, str):
        hosts_in = re.split(r"[\s,]+", hosts_in)
    hosts = []
    for h in hosts_in:
        h = h.strip().lower()
        if not h:
            continue
        h = re.sub(r"^https?://", "", h).split("/")[0]
        if h.startswith("www."):
            h = h[4:]
        if not _HOST_RE.match(h):
            raise ConfigError(f"'{h}' isn't a valid website address (use e.g. tiktok.com).")
        if h not in hosts:
            hosts.append(h)
    if not hosts:
        raise ConfigError("Add at least one website address (e.g. tiktok.com).")
    color = _text(p.get("color") or "#7C3AED", 7, "Color")
    if not _COLOR_RE.match(color):
        raise ConfigError("Color must look like #FF0000.")
    mode = p.get("mode") or "download"
    if mode not in MODES:
        raise ConfigError("Unknown mode.")
    name = _text(p.get("name"), 40, "Name", True)
    return {
        "key": key,
        "name": name,
        "slug": slug,
        "hosts": hosts,
        "color": color,
        "placeholder": _text(p.get("placeholder") or f"https://{hosts[0]}/...", 120, "Placeholder"),
        "title": _text(p.get("title") or f"{name} Downloader – Save Videos & Photos", 120, "Page title"),
        "description": _text(p.get("description") or f"Download {name} videos and photos in HD. Paste the link, pick a quality, save.", 300, "Page description"),
        "what": _text(p.get("what") or "videos and photos", 80, "What it downloads"),
        "enabled": bool(p.get("enabled", True)),
        "mode": mode,
        "notice": _text(p.get("notice"), 300, "Notice"),
    }


def clean_offer(o: dict) -> dict:
    oid = _text(o.get("id"), 31, "Offer id", True).lower()
    if not _KEY_RE.match(oid):
        raise ConfigError("Offer id may only use lowercase letters, numbers and dashes.")
    placements = [x for x in (o.get("placements") or []) if x in PLACEMENTS]
    return {
        "id": oid,
        "title": _text(o.get("title"), 80, "Offer title", True),
        "text": _text(o.get("text"), 240, "Offer text"),
        "url": _url(o.get("url"), "Affiliate link", True),
        "button": _text(o.get("button") or "Check it out", 30, "Button text"),
        "image": _url(o.get("image"), "Image link"),
        "badge": _text(o.get("badge"), 24, "Badge"),
        "placements": placements,
        "enabled": bool(o.get("enabled", True)),
    }


def clean_config(cfg: dict) -> dict:
    out = {
        "site_name": _text(cfg.get("site_name") or "SaveIt", 40, "Site name", True),
        "platforms": [clean_platform(p) for p in cfg.get("platforms", [])],
        "offers": [clean_offer(o) for o in cfg.get("offers", [])],
    }
    for field, items, label in (("key", out["platforms"], "platform key"), ("slug", out["platforms"], "page address"), ("id", out["offers"], "offer id")):
        seen = set()
        for it in items:
            if it[field] in seen:
                raise ConfigError(f"Two items use the same {label} '{it[field]}'.")
            seen.add(it[field])
    taken: dict[str, str] = {}
    for p in out["platforms"]:
        for h in p["hosts"]:
            if h in taken:
                raise ConfigError(f"{h} is used by both {taken[h]} and {p['name']}.")
            taken[h] = p["name"]
    return out


# ---------------------------------------------------------------- load / save
def _load_local() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load(from_github: bool = True) -> dict:
    """(Re)load settings. Prefers GitHub so admin changes survive restarts."""
    global _config
    cfg = None
    if from_github and github_enabled():
        try:
            text, _ = _gh_read(CONFIG_REPO_PATH)
            cfg = json.loads(text)
            log.info("loaded settings from GitHub %s", GITHUB_REPO)
        except Exception as e:  # network, token or JSON problem: fall back to the deployed file
            log.warning("couldn't load settings from GitHub (%s); using local file", e)
    if cfg is None:
        cfg = _load_local()
    try:
        cfg = clean_config(cfg)
    except ConfigError as e:
        log.error("settings file is invalid (%s); using local file", e)
        cfg = clean_config(_load_local())
    with _lock:
        _config = cfg
    return cfg


def get() -> dict:
    with _lock:
        if _config is None:
            load(from_github=False)
        return _config


def snapshot() -> dict:
    return copy.deepcopy(get())


def save(cfg: dict, message: str) -> dict:
    """Validate, apply immediately, write locally and commit to GitHub. Returns sync status."""
    global _config
    cfg = clean_config(cfg)
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    with _lock:
        _config = cfg
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
    if not github_enabled():
        status = {"ok": False, "message": "Saved on the server only. Add GITHUB_TOKEN in Render so changes survive restarts and update the github.io site."}
    else:
        try:
            _gh_write(CONFIG_REPO_PATH, text, f"Admin: {message}")
            status = {"ok": True, "message": "Saved. Live here now; the github.io site updates in about 2 minutes."}
        except Exception as e:
            log.exception("GitHub save failed")
            status = {"ok": False, "message": f"Applied here, but saving to GitHub failed ({_gh_err(e)}). It will be lost on restart."}
    status["at"] = time.time()
    _last_sync.update(status)
    return status


def _gh_err(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return {401: "token is invalid or expired", 403: "token lacks write access to this repo", 404: "repo or branch not found"}.get(e.code, f"HTTP {e.code}")
    return str(e)[:120]


def last_sync() -> dict:
    return dict(_last_sync)


# ---------------------------------------------------------------- lookups
def platforms(enabled_only: bool = True) -> list[dict]:
    return [p for p in get()["platforms"] if p["enabled"] or not enabled_only]


def platform_map(enabled_only: bool = True) -> dict[str, dict]:
    return {p["key"]: p for p in platforms(enabled_only)}


def offers_for(placement: str) -> list[dict]:
    return [o for o in get()["offers"] if o["enabled"] and placement in o["placements"]]


def detect_platform(url: str) -> str | None:
    """Platform key for a URL from an enabled, download-mode platform (else None)."""
    p = detect_any(url)
    return p["key"] if p and p["enabled"] and p["mode"] == "download" else None


def detect_any(url: str) -> dict | None:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    for p in get()["platforms"]:
        for h in p["hosts"]:
            if host == h or host.endswith("." + h):
                return p
    return None


# ---------------------------------------------------------------- click stats
_stats: dict[str, dict] = {}
_stats_dirty = False
_stats_loaded = False


def _load_stats():
    global _stats_loaded
    if _stats_loaded:
        return
    data = {}
    if github_enabled():
        try:
            data = json.loads(_gh_read(STATS_REPO_PATH)[0])
        except Exception:
            data = {}
    if not data:
        try:
            with open(STATS_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    with _lock:
        for k, v in data.items():
            if isinstance(v, dict):
                _stats[k] = {"clicks": int(v.get("clicks", 0)), "by_page": dict(v.get("by_page", {}))}
        _stats_loaded = True


def record_click(offer_id: str, page: str) -> bool:
    global _stats_dirty
    if offer_id not in {o["id"] for o in get()["offers"]}:
        return False
    page = re.sub(r"[^a-z0-9_-]", "", (page or "other").lower())[:40] or "other"
    with _lock:
        s = _stats.setdefault(offer_id, {"clicks": 0, "by_page": {}})
        s["clicks"] += 1
        s["by_page"][page] = s["by_page"].get(page, 0) + 1
        _stats_dirty = True
    return True


def stats() -> dict:
    with _lock:
        return copy.deepcopy(_stats)


def flush_stats() -> None:
    global _stats_dirty
    with _lock:
        if not _stats_dirty:
            return
        text = json.dumps(_stats, indent=2, sort_keys=True) + "\n"
        _stats_dirty = False
    try:
        with open(STATS_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    if github_enabled():
        try:
            _gh_write(STATS_REPO_PATH, text, "Update offer click stats")
        except Exception as e:
            log.warning("couldn't save click stats to GitHub: %s", _gh_err(e))
            with _lock:
                _stats_dirty = True


def start_background() -> None:
    """Load settings + stats from GitHub and flush stats periodically."""
    load()
    _load_stats()

    def loop():
        while True:
            time.sleep(STATS_FLUSH_SECONDS)
            flush_stats()

    threading.Thread(target=loop, daemon=True, name="stats-flush").start()
