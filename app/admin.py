"""Password-protected admin panel at /admin: platforms, affiliate offers, site settings."""
import asyncio
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from fastapi.concurrency import run_in_threadpool

from . import config, downloader, secrets_store

log = logging.getLogger("admin")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
if not os.getenv("SECRET_KEY"):
    log.warning("SECRET_KEY not set: admin logins reset whenever the server restarts")
if not ADMIN_PASSWORD:
    log.warning("ADMIN_PASSWORD not set: the admin panel is locked")

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=os.path.join(config.ROOT, "templates"))

_login_fails: dict[str, deque] = defaultdict(deque)


# ---------------------------------------------------------------- helpers
def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _require_login(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(401)


async def _form(request: Request) -> dict:
    """Logged-in POST: parse the form and check the CSRF token."""
    _require_login(request)
    form = await request.form()
    token = form.get("csrf", "")
    if not token or not hmac.compare_digest(str(token), request.session.get("csrf", "")):
        raise HTTPException(403)
    return form


def _flash(request: Request, message: str, ok: bool = True):
    request.session["flash"] = {"message": message, "ok": ok}


def _render(request: Request, name: str, **ctx):
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    ctx.update(
        request=request,
        csrf=request.session["csrf"],
        flash=request.session.pop("flash", None),
        site_name=config.get()["site_name"],
        github=config.github_enabled(),
        # only Render's free plan wipes the disk; on a VPS settings are kept without GitHub
        needs_github=bool(os.getenv("RENDER")) and not config.github_enabled(),
        placements=config.PLACEMENTS,
        nav=name.split("/")[-1].split("_")[0].replace(".html", ""),
    )
    return templates.TemplateResponse(request, f"admin/{name}", ctx)


def _go(path: str):
    return RedirectResponse(f"/admin{path}", status_code=303)


def _save(request: Request, cfg: dict, message: str, back: str):
    try:
        status = config.save(cfg, message)
    except config.ConfigError as e:
        _flash(request, str(e), ok=False)
        return None
    _flash(request, status["message"], ok=status["ok"])
    return _go(back)


# ---------------------------------------------------------------- login
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("admin"):
        return _go("")
    return _render(request, "login.html", locked=not ADMIN_PASSWORD)


@router.post("/login")
async def login(request: Request):
    ip = _ip(request)
    q = _login_fails[ip]
    now = time.time()
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= 5:
        _flash(request, "Too many wrong passwords. Try again in 10 minutes.", ok=False)
        return _go("/login")
    form = await request.form()
    pw = str(form.get("password", ""))
    if ADMIN_PASSWORD and hmac.compare_digest(pw.encode(), ADMIN_PASSWORD.encode()):
        request.session.clear()
        request.session["admin"] = True
        request.session["csrf"] = secrets.token_urlsafe(24)
        return _go("")
    q.append(now)
    _flash(request, "Wrong password.", ok=False)
    return _go("/login")


@router.post("/logout")
async def logout(request: Request):
    await _form(request)
    request.session.clear()
    return _go("/login")


# ---------------------------------------------------------------- dashboard
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    _require_login(request)
    cfg = config.get()
    st = config.stats()
    offers = sorted(
        ({"o": o, "clicks": st.get(o["id"], {}).get("clicks", 0), "by_page": st.get(o["id"], {}).get("by_page", {})} for o in cfg["offers"]),
        key=lambda x: -x["clicks"],
    )
    return _render(
        request, "dashboard.html",
        total_clicks=sum(x["clicks"] for x in offers),
        offers=offers,
        n_platforms=len(cfg["platforms"]),
        n_live=sum(1 for p in cfg["platforms"] if p["enabled"]),
        n_offers=sum(1 for o in cfg["offers"] if o["enabled"]),
        last_sync=config.last_sync(),
        has_secret=bool(os.getenv("SECRET_KEY")),
        downloads_now=downloader.active_jobs(),
    )


# ---------------------------------------------------------------- platforms
@router.get("/platforms", response_class=HTMLResponse)
def platforms_list(request: Request):
    _require_login(request)
    return _render(request, "platforms.html", items=config.get()["platforms"],
                   has_blocked_offers=bool(config.offers_for("blocked")))


@router.get("/platforms/new", response_class=HTMLResponse)
def platform_new(request: Request):
    _require_login(request)
    return _render(request, "platform_form.html", item={"enabled": True, "mode": "download", "color": "#7C3AED"}, original="")


@router.get("/platforms/{key}", response_class=HTMLResponse)
def platform_edit(request: Request, key: str):
    _require_login(request)
    item = next((p for p in config.get()["platforms"] if p["key"] == key), None)
    if not item:
        raise HTTPException(404)
    return _render(request, "platform_form.html", item=item, original=key)


@router.post("/platforms/save")
async def platform_save(request: Request):
    form = await _form(request)
    original = form.get("original", "")
    item = {
        "key": form.get("key", ""), "name": form.get("name", ""), "slug": form.get("slug", ""),
        "hosts": form.get("hosts", ""), "color": form.get("color", ""), "placeholder": form.get("placeholder", ""),
        "title": form.get("title", ""), "description": form.get("description", ""), "what": form.get("what", ""),
        "notice": form.get("notice", ""), "mode": form.get("mode", "download"), "enabled": form.get("enabled") == "on",
    }
    cfg = config.snapshot()
    if original:
        idx = next((i for i, p in enumerate(cfg["platforms"]) if p["key"] == original), None)
        if idx is None:
            raise HTTPException(404)
        cfg["platforms"][idx] = item
    else:
        cfg["platforms"].append(item)
    res = _save(request, cfg, f"{'update' if original else 'add'} platform {item['name'] or item['key']}", "/platforms")
    if res:
        return res
    # validation failed: show the form again with what they typed
    return _render(request, "platform_form.html", item=item, original=original)


@router.post("/platforms/{key}/{action}")
async def platform_action(request: Request, key: str, action: str):
    await _form(request)
    cfg = config.snapshot()
    items = cfg["platforms"]
    idx = next((i for i, p in enumerate(items) if p["key"] == key), None)
    if idx is None:
        raise HTTPException(404)
    name = items[idx]["name"]
    if action == "toggle":
        items[idx]["enabled"] = not items[idx]["enabled"]
        msg = f"{'enable' if items[idx]['enabled'] else 'disable'} {name}"
    elif action == "mode":
        items[idx]["mode"] = "offers" if items[idx]["mode"] == "download" else "download"
        msg = f"set {name} to {items[idx]['mode']} mode"
    elif action == "delete":
        items.pop(idx)
        msg = f"delete platform {name}"
    elif action in ("up", "down"):
        j = idx - 1 if action == "up" else idx + 1
        if 0 <= j < len(items):
            items[idx], items[j] = items[j], items[idx]
        msg = f"reorder platforms"
    else:
        raise HTTPException(404)
    return _save(request, cfg, msg, "/platforms") or _go("/platforms")


# ---------------------------------------------------------------- offers
@router.get("/offers", response_class=HTMLResponse)
def offers_list(request: Request):
    _require_login(request)
    st = config.stats()
    return _render(request, "offers.html", items=config.get()["offers"], stats=st)


@router.get("/offers/new", response_class=HTMLResponse)
def offer_new(request: Request):
    _require_login(request)
    return _render(request, "offer_form.html", item={"enabled": True, "placements": list(config.PLACEMENTS), "button": "Check it out"}, original="")


@router.get("/offers/{oid}", response_class=HTMLResponse)
def offer_edit(request: Request, oid: str):
    _require_login(request)
    item = next((o for o in config.get()["offers"] if o["id"] == oid), None)
    if not item:
        raise HTTPException(404)
    return _render(request, "offer_form.html", item=item, original=oid)


@router.post("/offers/save")
async def offer_save(request: Request):
    form = await _form(request)
    original = form.get("original", "")
    item = {
        "id": form.get("id", ""), "title": form.get("title", ""), "text": form.get("text", ""),
        "url": form.get("url", ""), "button": form.get("button", ""), "image": form.get("image", ""),
        "badge": form.get("badge", ""), "placements": form.getlist("placements"), "enabled": form.get("enabled") == "on",
    }
    cfg = config.snapshot()
    if original:
        idx = next((i for i, o in enumerate(cfg["offers"]) if o["id"] == original), None)
        if idx is None:
            raise HTTPException(404)
        cfg["offers"][idx] = item
    else:
        cfg["offers"].append(item)
    res = _save(request, cfg, f"{'update' if original else 'add'} offer {item['title'] or item['id']}", "/offers")
    return res or _render(request, "offer_form.html", item=item, original=original)


@router.post("/offers/{oid}/{action}")
async def offer_action(request: Request, oid: str, action: str):
    await _form(request)
    cfg = config.snapshot()
    items = cfg["offers"]
    idx = next((i for i, o in enumerate(items) if o["id"] == oid), None)
    if idx is None:
        raise HTTPException(404)
    title = items[idx]["title"]
    if action == "toggle":
        items[idx]["enabled"] = not items[idx]["enabled"]
        msg = f"{'enable' if items[idx]['enabled'] else 'pause'} offer {title}"
    elif action == "delete":
        items.pop(idx)
        msg = f"delete offer {title}"
    elif action in ("up", "down"):
        j = idx - 1 if action == "up" else idx + 1
        if 0 <= j < len(items):
            items[idx], items[j] = items[j], items[idx]
        msg = "reorder offers"
    else:
        raise HTTPException(404)
    return _save(request, cfg, msg, "/offers") or _go("/offers")


# ---------------------------------------------------------------- settings
TEST_LINKS = {
    "youtube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "instagram": "https://www.instagram.com/reel/C2eY6-5r9Xh/",
}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    _require_login(request)
    return _render(
        request, "settings.html", cfg=config.get(),
        cookies=secrets_store.cookies_info(),
        proxy=secrets_store.get_proxy(), proxy_masked=secrets_store.masked_proxy(),
        proxy_scopes=secrets_store.PROXY_SCOPES,
        test=request.session.pop("test_result", None),
    )


@router.post("/settings")
async def settings_save(request: Request):
    form = await _form(request)
    cfg = config.snapshot()
    cfg["site_name"] = form.get("site_name", "")
    cfg["brand_color"] = form.get("brand_color", "")
    cfg["downloads"] = {
        "max_filesize_mb": form.get("max_filesize_mb", ""),
        "max_concurrent": form.get("max_concurrent", ""),
        "rate_limit_per_min": form.get("rate_limit_per_min", ""),
    }
    try:
        cfg["menu_links"] = config.parse_menu_links(form.get("menu_links", ""))
    except config.ConfigError as e:
        _flash(request, str(e), ok=False)
        return _go("/settings")
    return _save(request, cfg, "update site settings", "/settings") or _go("/settings")


@router.post("/settings/cookies")
async def cookies_save(request: Request):
    form = await _form(request)
    text = str(form.get("cookies_text") or "")
    upload = form.get("cookies_file")
    if upload is not None and getattr(upload, "filename", ""):
        raw = await upload.read(secrets_store.MAX_COOKIES_BYTES + 1)
        text = raw.decode("utf-8", errors="ignore")
    if not text.strip():
        _flash(request, "Choose a cookies.txt file or paste its text first.", ok=False)
        return _go("/settings#youtube")
    try:
        info = secrets_store.save_cookies(text)
    except secrets_store.SecretError as e:
        _flash(request, str(e), ok=False)
        return _go("/settings#youtube")
    sites = ", ".join(info.get("sites") or []) or "unknown sites"
    _flash(request, f"Cookies saved ({sites}). They're used from the next download. Use “Test YouTube” to check.")
    return _go("/settings#youtube")


@router.post("/settings/cookies/delete")
async def cookies_delete(request: Request):
    await _form(request)
    secrets_store.delete_cookies()
    left = secrets_store.cookies_info()
    _flash(request, "Cookies removed." + (" (A cookies file mounted on the server is still used.)" if left["present"] else ""))
    return _go("/settings#youtube")


@router.post("/settings/proxy")
async def proxy_save(request: Request):
    form = await _form(request)
    url = str(form.get("proxy_url") or "").strip() or secrets_store.get_proxy()["url"]  # blank keeps the current one
    try:
        secrets_store.save_proxy(url, str(form.get("proxy_scope") or "youtube"))
    except secrets_store.SecretError as e:
        _flash(request, str(e), ok=False)
        return _go("/settings#youtube")
    _flash(request, "Proxy saved." if url else "No proxy set.")
    return _go("/settings#youtube")


@router.post("/settings/proxy/delete")
async def proxy_delete(request: Request):
    await _form(request)
    secrets_store.save_proxy("", "youtube")
    _flash(request, "Proxy removed.")
    return _go("/settings#youtube")


@router.post("/settings/test")
async def settings_test(request: Request):
    form = await _form(request)
    which = form.get("platform", "youtube")
    url = TEST_LINKS.get(which, TEST_LINKS["youtube"])
    name = {"youtube": "YouTube", "instagram": "Instagram"}.get(which, "YouTube")
    started = time.time()
    try:
        info = await asyncio.wait_for(run_in_threadpool(downloader.get_info, url), timeout=60)
        opts = info["items"][0]["options"] if info.get("items") else []
        best = next((o["label"] for o in opts if o.get("kind") == "video"), "no video options")
        result = {"ok": True, "message": f"{name} works: “{info.get('title', '')[:60]}” — best quality {best} ({time.time() - started:.0f}s)."}
    except asyncio.TimeoutError:
        result = {"ok": False, "message": f"{name} test timed out after 60 seconds."}
    except downloader.DownloadError as e:
        result = {"ok": False, "message": f"{name} failed: {e}"}
    request.session["test_result"] = result
    return _go("/settings#youtube")
