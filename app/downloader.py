"""Thin wrapper around yt-dlp: fetch media info and download a chosen option."""
import os
import shutil
import tempfile
from dataclasses import dataclass

import yt_dlp

MAX_FILESIZE = int(os.getenv("MAX_FILESIZE_MB", "500")) * 1024 * 1024
COOKIES_FILE = os.getenv("COOKIES_FILE")  # optional Netscape cookies.txt for login-only posts

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "heic"}


class DownloadError(Exception):
    pass


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "socket_timeout": 20,
        "max_filesize": MAX_FILESIZE,
        "restrictfilenames": True,
        "default_search": None,  # never treat input as a search query
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


def _human_size(n):
    if not n:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _is_image(entry: dict) -> bool:
    ext = (entry.get("ext") or "").lower()
    return ext in IMAGE_EXTS or (not entry.get("formats") and ext in IMAGE_EXTS)


def _options_for(entry: dict) -> list[dict]:
    """Build the user-facing download choices for one media item."""
    if _is_image(entry):
        return [{"id": "image", "label": f"Image ({(entry.get('ext') or 'jpg').upper()})", "ext": entry.get("ext") or "jpg"}]

    formats = entry.get("formats") or []
    has_audio = any(f.get("acodec") not in (None, "none") for f in formats) or not formats
    options = [{"id": "best", "label": "Best quality (MP4)", "ext": "mp4"}]

    # One option per distinct video height, best first
    seen = set()
    heights = sorted({f.get("height") for f in formats if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    for h in heights:
        if h in seen or h < 144:
            continue
        seen.add(h)
        size = max((f.get("filesize") or f.get("filesize_approx") or 0) for f in formats if f.get("height") == h) or None
        label = f"{h}p MP4"
        if size:
            label += f" · ~{_human_size(size)}"
        options.append({"id": f"h{h}", "label": label, "ext": "mp4"})

    if has_audio:
        options.append({"id": "audio", "label": "Audio only (MP3)", "ext": "mp3"})
    return options


def _item(entry: dict, index: int) -> dict:
    thumb = entry.get("thumbnail")
    if not thumb and entry.get("thumbnails"):
        thumb = entry["thumbnails"][-1].get("url")
    if not thumb and _is_image(entry):
        thumb = entry.get("url")
    return {
        "index": index,
        "title": entry.get("title") or (entry.get("description") or "")[:80] or "Media",
        "thumbnail": thumb,
        "duration": entry.get("duration"),
        "type": "image" if _is_image(entry) else "video",
        "options": _options_for(entry),
    }


def get_info(url: str) -> dict:
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_friendly(str(e))) from e

    if info.get("_type") == "playlist" or info.get("entries"):
        entries = [e for e in (info.get("entries") or []) if e]
        if len(entries) > 50:
            entries = entries[:50]
        items = [_item(e, i + 1) for i, e in enumerate(entries)]
    else:
        items = [_item(info, 0)]

    return {
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": items[0]["thumbnail"] if items else info.get("thumbnail"),
        "items": items,
    }


def _format_selector(option: str) -> dict:
    if option == "audio":
        return {
            "format": "bestaudio/best",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        }
    if option.startswith("h") and option[1:].isdigit():
        h = int(option[1:])
        return {
            "format": f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b",
            "merge_output_format": "mp4",
        }
    if option == "image":
        return {"format": "best"}
    # "best"
    return {"format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b", "merge_output_format": "mp4"}


@dataclass
class DownloadedFile:
    path: str
    filename: str
    workdir: str

    def cleanup(self):
        shutil.rmtree(self.workdir, ignore_errors=True)


def download(url: str, option: str, item: int = 0) -> DownloadedFile:
    workdir = tempfile.mkdtemp(prefix="dl_")
    opts = _base_opts()
    opts.update(_format_selector(option))
    opts["outtmpl"] = os.path.join(workdir, "%(title).80B [%(id)s].%(ext)s")
    if item > 0:
        opts["playlist_items"] = str(item)
    else:
        opts["noplaylist"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise DownloadError(_friendly(str(e))) from e

    files = [f for f in os.listdir(workdir) if not f.endswith((".part", ".ytdl", ".temp"))]
    if not files:
        shutil.rmtree(workdir, ignore_errors=True)
        raise DownloadError("Nothing was downloaded. The file may be larger than the size limit.")
    files.sort(key=lambda f: os.path.getsize(os.path.join(workdir, f)), reverse=True)
    name = files[0]
    return DownloadedFile(path=os.path.join(workdir, name), filename=name, workdir=workdir)


def _friendly(msg: str) -> str:
    m = msg.lower()
    if "login" in m or "cookies" in m or "private" in m:
        return "This post is private or needs a login. Only public posts can be downloaded."
    if "unsupported url" in m:
        return "That link isn't a supported post URL."
    if "not available" in m or "removed" in m or "404" in m:
        return "This media isn't available — it may have been deleted or restricted."
    if "rate" in m and "limit" in m:
        return "The platform is rate-limiting requests. Please try again in a minute."
    if "no video" in m:
        return "No downloadable media was found in that post."
    return "Couldn't fetch this link. Check it's a public post and try again."
