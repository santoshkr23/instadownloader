"""Thin wrapper around yt-dlp: fetch media info and download a chosen option."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import yt_dlp

MAX_FILESIZE = int(os.getenv("MAX_FILESIZE_MB", "500")) * 1024 * 1024
# Optional Netscape cookies.txt for login-walled posts / YouTube bot checks.
# On Render, add it as a Secret File named cookies.txt and it is picked up automatically.
COOKIES_FILE = os.getenv("COOKIES_FILE") or "/etc/secrets/cookies.txt"

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "heic"}

log = logging.getLogger("downloader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
if os.path.exists(COOKIES_FILE):
    log.info("cookies file found at %s", COOKIES_FILE)
else:
    log.info("no cookies file (%s) — YouTube may ask this server to 'confirm you're not a bot'", COOKIES_FILE)


class DownloadError(Exception):
    pass


def _base_opts(workdir: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": False,
        "socket_timeout": 20,
        "max_filesize": MAX_FILESIZE,
        "restrictfilenames": True,
        "default_search": None,  # never treat input as a search query
    }
    if workdir and COOKIES_FILE and os.path.exists(COOKIES_FILE):
        # yt-dlp writes cookies back when it finishes; Render's secret files are read-only
        # and requests run in parallel, so each request gets its own copy.
        copy = os.path.join(workdir, ".cookies.txt")
        shutil.copyfile(COOKIES_FILE, copy)
        opts["cookiefile"] = copy
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
    videos = [f for f in formats if f.get("vcodec") not in (None, "none") and (f.get("height") or f.get("width"))]
    audios = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    has_audio = bool(audios) or any(f.get("acodec") not in (None, "none") for f in formats) or not formats
    audio_size = max(((a.get("filesize") or a.get("filesize_approx") or 0) for a in audios), default=0)

    # Group by quality tier using the short side, so a vertical 1080x1920 Short counts as 1080p
    by_tier: dict[int, int] = {}
    for f in videos:
        tier = _tier(f)
        if not tier:
            continue
        size = (f.get("filesize") or f.get("filesize_approx") or 0)
        by_tier[tier] = max(by_tier.get(tier, 0), size)

    options = []
    for tier in sorted(by_tier, reverse=True):
        label = f"{TIER_NAMES.get(tier, f'{tier}p')} MP4"
        size = by_tier[tier] + (audio_size if by_tier[tier] else 0)
        if size:
            label += f" · ~{_human_size(size)}"
        options.append({"id": f"h{tier}", "label": label, "ext": "mp4", "kind": "video"})
    if not options:
        options.append({"id": "best", "label": "Best quality MP4", "ext": "mp4", "kind": "video"})

    if has_audio:
        for kbps in MP3_BITRATES:
            options.append({"id": f"mp3_{kbps}", "label": f"MP3 · {kbps} kbps", "ext": "mp3", "kind": "audio"})
    return options


TIERS = [4320, 2160, 1440, 1080, 720, 480, 360, 240, 144]
TIER_NAMES = {4320: "8K (4320p)", 2160: "4K (2160p)", 1440: "2K (1440p)", 1080: "1080p HD", 720: "720p HD"}
MP3_BITRATES = (320, 192, 128)


def _tier(f: dict) -> int | None:
    """Snap a format's short side to the nearest standard resolution (e.g. 1072 → 1080)."""
    w, h = f.get("width"), f.get("height")
    short = min(x for x in (w, h) if x) if (w or h) else None
    if not short or short < 120:
        return None
    return min(TIERS, key=lambda t: abs(t - short))


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
    tmp = tempfile.mkdtemp(prefix="info_")
    try:
        with yt_dlp.YoutubeDL(_base_opts(tmp)) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        log.warning("info failed for %s: %s", url, e)
        raise DownloadError(_friendly(str(e), url)) from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
    if option == "audio" or option.startswith("mp3_"):
        kbps = option.split("_", 1)[1] if "_" in option else "192"
        return {
            "format": "bestaudio/best",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": kbps}],
        }
    if option.startswith("h") and option[1:].isdigit():
        h = int(option[1:])
        # "res" sorts by the short side, so this picks the best stream at or below the
        # requested tier (works for vertical Shorts/Reels too), preferring H.264/AAC
        # where that resolution offers it, since it plays everywhere.
        return {
            "format": "bv*+ba/b",
            "format_sort": [f"res:{h}", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
        }
    if option == "image":
        return {"format": "best"}
    # "best"
    return {"format": "bv*+ba/b", "format_sort": ["res", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"], "merge_output_format": "mp4"}


@dataclass
class DownloadedFile:
    path: str
    filename: str
    workdir: str

    def cleanup(self):
        shutil.rmtree(self.workdir, ignore_errors=True)


def download(url: str, option: str, item: int = 0) -> DownloadedFile:
    workdir = tempfile.mkdtemp(prefix="dl_")
    opts = _base_opts(workdir)
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
        log.warning("download failed for %s (%s): %s", url, option, e)
        shutil.rmtree(workdir, ignore_errors=True)
        raise DownloadError(_friendly(str(e), url)) from e

    files = [f for f in os.listdir(workdir)
             if not f.startswith(".") and not f.endswith((".part", ".ytdl", ".temp"))]
    if not files:
        shutil.rmtree(workdir, ignore_errors=True)
        raise DownloadError("Nothing was downloaded. The file may be larger than the size limit.")
    files.sort(key=lambda f: os.path.getsize(os.path.join(workdir, f)), reverse=True)
    path = os.path.join(workdir, files[0])
    if path.lower().endswith(".mp4"):
        try:
            path = make_playable(path)
        except Exception:
            shutil.rmtree(workdir, ignore_errors=True)
            raise DownloadError("Couldn't convert this video to a playable format. Please try a lower quality.")
    return DownloadedFile(path=path, filename=os.path.basename(path), workdir=workdir)


# ---------- make sure the MP4 actually plays everywhere ----------
# Instagram and YouTube often serve AV1 or VP9 video. Many phones, WhatsApp and
# Windows' built-in player can't decode those and play only the sound, so any MP4
# whose video isn't H.264 (or whose audio isn't AAC) gets converted.
PLAYABLE_VIDEO = {"h264"}
PLAYABLE_AUDIO = {"aac", "mp3"}
REENCODE_MAX_SHORT_SIDE = int(os.getenv("REENCODE_MAX_SHORT_SIDE", "1080"))


def _probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", path],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    streams = json.loads(out).get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {"video": v, "audio": a}


def make_playable(path: str) -> str:
    info = _probe(path)
    v, a = info["video"], info["audio"]
    if not v:
        return path  # audio-only file; nothing to fix
    v_ok = v.get("codec_name") in PLAYABLE_VIDEO
    a_ok = a is None or a.get("codec_name") in PLAYABLE_AUDIO
    if v_ok and a_ok:
        return path

    short_side = min(x for x in (v.get("width"), v.get("height")) if x) if (v.get("width") or v.get("height")) else 0
    # Re-encoding 1440p/4K on a small server takes too long, so those stay as they are
    # (they play in VLC, Chrome and recent phones).
    if not v_ok and short_side > REENCODE_MAX_SHORT_SIDE:
        v_ok = True
        if a_ok:
            return path

    out = path[:-4] + ".h264.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", path, "-map", "0:v:0", "-map", "0:a:0?"]
    cmd += ["-c:v", "copy"] if v_ok else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy"] if a_ok else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, timeout=1800, capture_output=True)
    os.remove(path)
    final = path  # keep the original nice filename
    os.replace(out, final)
    return final


def _friendly(msg: str, url: str = "") -> str:
    m = msg.lower().replace("’", "'")
    # YouTube shows this to cloud-server IPs even for public videos
    if "not a bot" in m or "sign in to confirm" in m:
        return ("YouTube is temporarily blocking our download server (its 'confirm you're not a bot' check). "
                "The video is fine; please try again later.")
    if "private" in m:
        return "This post is private. Only public posts can be downloaded."
    if "login" in m or "log in" in m or "sign in" in m or "cookies" in m:
        site = "Instagram" if "instagram" in url else "This site"
        return f"{site} is asking our server to log in before it shares this post. Please try again later."
    if "unsupported url" in m:
        return "That link isn't a supported post URL."
    if "not available" in m or "removed" in m or "404" in m:
        return "This media isn't available — it may have been deleted or restricted."
    if "rate" in m and "limit" in m:
        return "The platform is rate-limiting requests. Please try again in a minute."
    if "no video" in m:
        return "No downloadable media was found in that post."
    return "Couldn't fetch this link. Check it's a public post and try again."
