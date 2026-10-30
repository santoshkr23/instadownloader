import os
import shutil
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.middleware.sessions import SessionMiddleware

from . import admin, config, downloader, pages

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = os.getenv("SITE_URL", "http://localhost:8000").rstrip("/")
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(title="Downloader", docs_url=None, redoc_url=None)
# signed cookie for the admin login; SECRET_KEY must be set in production
app.add_middleware(SessionMiddleware, secret_key=admin.SECRET_KEY, session_cookie="admin_session",
                   max_age=12 * 3600, same_site="strict", https_only=SITE_URL.startswith("https"))
# lets the static GitHub Pages frontend call this server
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition"],
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ---- simple per-IP rate limiter (in memory; use Redis if you run several instances) ----
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def _check_rate(request: Request):
    ip = _client_ip(request)
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
    q.append(now)


def _validate(url: str) -> str:
    url = (url or "").strip()
    if len(url) > 2048:
        raise HTTPException(400, "That link is too long.")
    platform = config.detect_platform(url)
    if platform:
        return platform
    p = config.detect_any(url)
    if p and p["enabled"]:
        raise HTTPException(400, p["notice"] or f"{p['name']} downloads aren't available right now.")
    names = [x["name"] for x in config.platforms() if x["mode"] == "download"]
    raise HTTPException(400, "Paste a link from " + (", ".join(names) or "a supported site") + ".")


def _page_ctx(request: Request, key: str | None):
    ctx = pages.page_context(key, base="", api_base="", site_url=SITE_URL)
    ctx["request"] = request
    return ctx


@app.on_event("startup")
def _startup():
    config.start_background()


@app.on_event("shutdown")
def _shutdown():
    config.flush_stats()


# ---------------- pages ----------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", _page_ctx(request, None))


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\nSitemap: {SITE_URL}/sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap():
    urls = [f"{SITE_URL}/"] + [f"{SITE_URL}/{p['slug']}/" for p in config.platforms()]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    body += "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n"
    return PlainTextResponse(body, media_type="application/xml")


@app.get("/health")
def health():
    import yt_dlp.version
    return {
        "ok": True,
        "version": (os.getenv("RENDER_GIT_COMMIT") or "local")[:7],  # matches the GitHub commit Render is running
        "yt_dlp": yt_dlp.version.__version__,
        "deno": shutil.which("deno") is not None,
        "cookies": os.path.exists(downloader.COOKIES_FILE),
        "settings_saved_to_github": config.github_enabled(),
    }


# ---------------- API ----------------
class InfoRequest(BaseModel):
    url: str


@app.post("/api/info")
async def api_info(body: InfoRequest, request: Request):
    _check_rate(request)
    platform = _validate(body.url)
    try:
        info = await run_in_threadpool(downloader.get_info, body.url.strip())
    except downloader.DownloadError as e:
        raise HTTPException(422, str(e))
    info["platform"] = platform
    return info


@app.get("/api/download")
async def api_download(
    request: Request,
    url: str = Query(...),
    option: str = Query("best", pattern=r"^(best|audio|image|h\d{3,4}|mp3_(128|192|320))$"),
    item: int = Query(0, ge=0, le=50),
):
    _check_rate(request)
    _validate(url)
    try:
        f = await run_in_threadpool(downloader.download, url.strip(), option, item)
    except downloader.DownloadError as e:
        raise HTTPException(422, str(e))
    return FileResponse(
        f.path,
        filename=f.filename,
        media_type="application/octet-stream",
        background=BackgroundTask(f.cleanup),
    )


# offer click counter, called with navigator.sendBeacon when someone clicks an offer
_click_hits: dict[str, deque] = defaultdict(deque)


@app.post("/api/click/{offer_id}")
def api_click(offer_id: str, request: Request, page: str = "other"):
    q = _click_hits[_client_ip(request)]
    now = time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) < 30 and config.record_click(offer_id[:40], page):
        q.append(now)
    return Response(status_code=204)


app.include_router(admin.router)


@app.exception_handler(RequestValidationError)
async def bad_params(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "Invalid request."}, status_code=400)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/admin") and exc.status_code in (401, 403):
        return RedirectResponse("/admin/login", status_code=303)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 404:
        ctx = _page_ctx(request, None)
        ctx["page_title"] = "Page not found"
        return templates.TemplateResponse(request, "404.html", ctx, status_code=404)
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)


# platform pages last so they don't shadow the fixed routes above
@app.get("/{slug}", response_class=HTMLResponse)
@app.get("/{slug}/", response_class=HTMLResponse, include_in_schema=False)
def platform_page(request: Request, slug: str):
    key = next((p["key"] for p in config.platforms() if p["slug"] == slug), None)
    if not key:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request, "index.html", _page_ctx(request, key))
