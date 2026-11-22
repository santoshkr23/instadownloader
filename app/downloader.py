"""Thin wrapper around yt-dlp: fetch media info, and run downloads as background jobs
that report their progress so the page can show a percentage."""
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import yt_dlp

from . import config, secrets_store

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "heic"}

log = logging.getLogger("downloader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class DownloadError(Exception):
    pass


def _base_opts(workdir: str | None = None, url: str = "") -> dict:
    opts = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": False,
        "socket_timeout": 20,
        "max_filesize": config.downloads()["max_filesize_mb"] * 1024 * 1024,
        "restrictfilenames": True,
        "default_search": None,  # never treat input as a search query
    }
    proxy = secrets_store.proxy_for(url) if url else None
    if proxy:
        opts["proxy"] = proxy
    src = secrets_store.cookies_path()
    if workdir and src:
        # yt-dlp writes cookies back when it finishes, and requests run in parallel,
        # so each request gets its own copy.
        copy = os.path.join(workdir, ".cookies.txt")
        shutil.copyfile(src, copy)
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
        with yt_dlp.YoutubeDL(_base_opts(tmp, url)) as ydl:
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


def _prepare(url: str, option: str, item: int, workdir: str, progress=None) -> dict:
    opts = _base_opts(workdir, url)
    opts.update(_format_selector(option))
    opts["outtmpl"] = os.path.join(workdir, "%(title).80B [%(id)s].%(ext)s")
    if item > 0:
        opts["playlist_items"] = str(item)
    else:
        opts["noplaylist"] = True
    if progress:
        opts["progress_hooks"] = [progress.on_download]
        opts["postprocessor_hooks"] = [progress.on_postprocess]
    return opts


def _finish(workdir: str, progress=None) -> DownloadedFile:
    files = [f for f in os.listdir(workdir)
             if not f.startswith(".") and not f.endswith((".part", ".ytdl", ".temp"))]
    if not files:
        raise DownloadError("Nothing was downloaded. The file may be larger than the size limit.")
    files.sort(key=lambda f: os.path.getsize(os.path.join(workdir, f)), reverse=True)
    path = os.path.join(workdir, files[0])
    if path.lower().endswith(".mp4"):
        try:
            path = make_playable(path, progress)
        except Exception:
            log.exception("make_playable failed")
            raise DownloadError("Couldn't convert this video to a playable format. Please try a lower quality.")
    return DownloadedFile(path=path, filename=os.path.basename(path), workdir=workdir)


def download(url: str, option: str, item: int = 0) -> DownloadedFile:
    """Download in one go (no progress). Kept for the old /api/download link."""
    workdir = tempfile.mkdtemp(prefix="dl_")
    try:
        with yt_dlp.YoutubeDL(_prepare(url, option, item, workdir)) as ydl:
            ydl.download([url])
        return _finish(workdir)
    except yt_dlp.utils.DownloadError as e:
        log.warning("download failed for %s (%s): %s", url, option, e)
        shutil.rmtree(workdir, ignore_errors=True)
        raise DownloadError(_friendly(str(e), url)) from e
    except DownloadError:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


# ---------- background jobs with progress ----------
JOB_TTL = 30 * 60          # finished files are kept this long for the browser to fetch
MAX_JOBS_PER_IP = 2


@dataclass
class Job:
    id: str
    url: str
    option: str
    item: int
    ip: str
    created: float = field(default_factory=time.time)
    status: str = "queued"      # queued | downloading | processing | done | error
    stage: str = "Waiting in line…"
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    error: str = ""
    file: DownloadedFile | None = None
    finished: float = 0.0
    # progress bookkeeping
    parts: int = 1
    part_names: list = field(default_factory=list)
    part_frac: dict = field(default_factory=dict)
    part_weight: dict = field(default_factory=dict)
    kind: str = "video"

    def public(self) -> dict:
        d = {"id": self.id, "status": self.status, "stage": self.stage, "percent": round(self.percent, 1),
             "speed": self.speed, "eta": self.eta}
        if self.status == "error":
            d["error"] = self.error
        if self.status == "done" and self.file:
            d["filename"] = self.file.filename
            d["size"] = _human_size(os.path.getsize(self.file.path)) if os.path.exists(self.file.path) else None
        return d


class _Progress:
    """Turns yt-dlp/ffmpeg callbacks into one 0-100 number.
    Downloading the stream(s) = 0-90%, merging/converting = 90-99%, done = 100%."""

    def __init__(self, job: Job):
        self.job = job

    def _set(self, pct: float, stage: str, speed: str = "", eta: str = ""):
        j = self.job
        j.percent = max(j.percent, min(pct, 99.0))   # never jumps backwards
        j.stage, j.speed, j.eta = stage, speed, eta

    def on_download(self, d: dict):
        j = self.job
        if d.get("status") not in ("downloading", "finished"):
            return
        info = d.get("info_dict") or {}
        name = d.get("filename") or d.get("tmpfilename") or ""
        if name not in j.part_names:
            j.part_names.append(name)
        idx = j.part_names.index(name)
        parts = max(j.parts, len(j.part_names))
        is_audio = info.get("vcodec") in (None, "none") and info.get("acodec") not in (None, "none")
        # audio is much smaller than video, so it counts for less of the bar
        j.part_weight[name] = (0.15 if is_audio else 0.85) if parts == 2 else 1 / parts
        if d["status"] == "finished":
            frac = 1.0
        else:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                frac = min((d.get("downloaded_bytes") or 0) / total, 1.0)
            elif d.get("fragment_count"):
                frac = min((d.get("fragment_index") or 0) / d["fragment_count"], 1.0)
            else:
                frac = 0.0
        j.status = "downloading"
        j.part_frac[name] = frac
        what = "audio" if is_audio or j.kind == "audio" else "video"
        stage = f"Downloading {what}" + (f" ({idx + 1} of {parts})" if parts > 1 else "")
        done = sum(j.part_weight.get(n, 1 / parts) * f for n, f in j.part_frac.items())
        self._set(done * 90, stage, _speed(d.get("speed")), _eta(d.get("eta")))

    def on_postprocess(self, d: dict):
        if d.get("status") != "started":
            return
        pp = d.get("postprocessor") or ""
        stage = {"Merger": "Joining video and audio", "ExtractAudio": "Converting to MP3"}.get(pp)
        if stage:
            self.job.status = "processing"
            self._set(max(self.job.percent, 90), stage)

    def converting(self, frac: float):
        self.job.status = "processing"
        self._set(90 + 9 * frac, "Converting so it plays on every phone")


_executor: ThreadPoolExecutor | None = None
_executor_size = 0
_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor, _executor_size
    size = config.downloads()["max_concurrent"]
    if _executor is None or size != _executor_size:
        old = _executor
        _executor, _executor_size = ThreadPoolExecutor(max_workers=size, thread_name_prefix="dl"), size
        if old:
            old.shutdown(wait=False)
    return _executor


def start_job(url: str, option: str, item: int, ip: str) -> Job:
    with _jobs_lock:
        active = [j for j in _jobs.values() if j.ip == ip and j.status in ("queued", "downloading", "processing")]
        if len(active) >= MAX_JOBS_PER_IP:
            raise DownloadError("You already have 2 downloads running. Please wait for one to finish.")
        job = Job(id=secrets.token_urlsafe(18), url=url, option=option, item=item, ip=ip,
                  kind="audio" if option.startswith("mp3") or option == "audio" else "video")
        _jobs[job.id] = job
    _pool().submit(_run, job)
    return job


def active_jobs() -> int:
    with _jobs_lock:
        return sum(1 for j in _jobs.values() if j.status in ("queued", "downloading", "processing"))


def get_job(job_id: str) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _run(job: Job):
    workdir = tempfile.mkdtemp(prefix="job_")
    progress = _Progress(job)
    job.status, job.stage = "downloading", "Getting video details…"
    try:
        with yt_dlp.YoutubeDL(_prepare(job.url, job.option, job.item, workdir, progress)) as ydl:
            info = ydl.extract_info(job.url, download=False)
            entry = info
            if info.get("_type") == "playlist" or info.get("entries"):
                entries = [e for e in (info.get("entries") or []) if e]
                entry = entries[0] if entries else info
            job.parts = len(entry.get("requested_formats") or []) or 1
            job.stage = "Starting download…"
            ydl.process_ie_result(info, download=True)
        job.file = _finish(workdir, progress)
        job.status, job.stage, job.percent, job.speed, job.eta = "done", "Ready", 100.0, "", ""
    except yt_dlp.utils.DownloadError as e:
        log.warning("job failed for %s (%s): %s", job.url, job.option, e)
        job.status, job.error = "error", _friendly(str(e), job.url)
        shutil.rmtree(workdir, ignore_errors=True)
    except DownloadError as e:
        job.status, job.error = "error", str(e)
        shutil.rmtree(workdir, ignore_errors=True)
    except Exception:
        log.exception("job crashed for %s", job.url)
        job.status, job.error = "error", "Something went wrong while downloading. Please try again."
        shutil.rmtree(workdir, ignore_errors=True)
    finally:
        job.finished = time.time()


def cleanup_jobs():
    """Delete finished jobs (and their files) after JOB_TTL."""
    now = time.time()
    with _jobs_lock:
        old = [j for j in _jobs.values() if j.finished and now - j.finished > JOB_TTL]
        for j in old:
            _jobs.pop(j.id, None)
    for j in old:
        if j.file:
            j.file.cleanup()


def start_cleanup_thread():
    def loop():
        while True:
            time.sleep(60)
            try:
                cleanup_jobs()
            except Exception:
                log.exception("cleanup failed")
    threading.Thread(target=loop, daemon=True, name="job-cleanup").start()


def _speed(bps) -> str:
    if not bps:
        return ""
    return f"{bps / 1048576:.1f} MB/s" if bps >= 1048576 else f"{bps / 1024:.0f} KB/s"


def _eta(sec) -> str:
    if not sec:
        return ""
    sec = int(sec)
    return f"{sec // 60}m {sec % 60}s left" if sec >= 60 else f"{sec}s left"


# ---------- make sure the MP4 actually plays everywhere ----------
# Instagram and YouTube often serve AV1 or VP9 video. Many phones, WhatsApp and
# Windows' built-in player can't decode those and play only the sound, so any MP4
# whose video isn't H.264 (or whose audio isn't AAC) gets converted.
PLAYABLE_VIDEO = {"h264"}
PLAYABLE_AUDIO = {"aac", "mp3"}
REENCODE_MAX_SHORT_SIDE = int(os.getenv("REENCODE_MAX_SHORT_SIDE", "1080"))


def _duration(path: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (ValueError, subprocess.SubprocessError):
        return 0.0


def _run_ffmpeg_with_progress(cmd: list, duration: float, progress) -> None:
    """Run ffmpeg and report how far through the video it is."""
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    start = time.time()
    try:
        for line in proc.stdout:
            m = re.match(r"out_time_us=(\d+)", line) or re.match(r"out_time_ms=(\d+)", line)
            if m and duration > 0:
                progress.converting(min(int(m.group(1)) / 1e6 / duration, 1.0))
            if time.time() - start > 1800:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, 1800)
        if proc.wait() != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=proc.stderr.read()[-500:])
    finally:
        if proc.poll() is None:
            proc.kill()


def _probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", path],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    streams = json.loads(out).get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {"video": v, "audio": a}


def make_playable(path: str, progress=None) -> str:
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
    if progress is None:
        subprocess.run(cmd, check=True, timeout=1800, capture_output=True)
    else:
        _run_ffmpeg_with_progress(cmd, _duration(path), progress)
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
    if "failed to resolve" in m or "name or service not known" in m or "connection refused" in m or "timed out" in m:
        return "Our server couldn't reach the site (network problem). Please try again in a minute."
    if "unsupported url" in m:
        return "That link isn't a supported post URL."
    if "not available" in m or "removed" in m or "404" in m:
        return "This media isn't available — it may have been deleted or restricted."
    if "rate" in m and "limit" in m:
        return "The platform is rate-limiting requests. Please try again in a minute."
    if "no video" in m:
        return "No downloadable media was found in that post."
    return "Couldn't fetch this link. Check it's a public post and try again."
