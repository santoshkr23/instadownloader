# InstaDownloader (SaveIt)

A free web app to download media from **Instagram, YouTube, Pinterest, Twitter/X and Facebook** — paste a link, pick a quality, save.

- Instagram: Reels, videos, photos, carousel posts (each item downloadable)
- YouTube: videos & Shorts from 360p up to 4K, or MP3 at 320/192/128 kbps (needs Deno, installed via requirements)
- Pinterest: video pins and images (incl. `pin.it` short links)
- Twitter / X: videos and GIFs from posts
- Facebook: public videos and Reels

Each platform has its own SEO page (`/instagram-downloader`, `/youtube-downloader`, `/pinterest-downloader`, `/twitter-downloader`, `/facebook-downloader`) plus `sitemap.xml` and `robots.txt`.

## Stack

- **Backend:** FastAPI + [yt-dlp](https://github.com/yt-dlp/yt-dlp) (does the actual extraction) + Deno (required by yt-dlp for YouTube) + ffmpeg (merging HD video/audio, MP3, converting AV1/VP9 to H.264 so videos don't play as sound-only)
- **Frontend:** server-rendered Jinja templates, plain CSS/JS, light & dark mode, mobile-first

## Run locally

```bash
# needs Python 3.11+ and ffmpeg on PATH
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000
```

Or with Docker:

```bash
docker build -t instadownloader .
docker run -p 8000:8000 instadownloader
```

## Deploy

The app has two parts:

1. **Website → GitHub Pages** (`https://santoshkr23.github.io/instadownloader/`).
   Every push to `main` runs `.github/workflows/pages.yml`, which builds static pages with `build_static.py` and publishes them to the `gh-pages` branch. If Pages isn't on yet: Settings → Pages → Source "Deploy from a branch" → `gh-pages` / root.
2. **Download server → Render** (free plan). On render.com: New → Blueprint → pick this repo → Apply. `render.yaml` sets everything up. The free plan sleeps after ~15 min idle, so the first request after that takes 30–60 s.

If your Render URL isn't `https://instadownloader.onrender.com`, add a repo variable `API_BASE` with the real URL (Settings → Secrets and variables → Actions → Variables) and re-run the workflow.

The server also serves the full site itself, so you can skip GitHub Pages and use the Render URL directly. Railway, Fly.io, or any VPS with Docker works too.

Note: datacenter IPs are often rate-limited or blocked by Instagram and YouTube. If you see "needs a login" / "rate-limiting" errors in production, set `COOKIES_FILE` to a cookies.txt from a throwaway account, or route through a residential proxy.

## Config (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `SITE_NAME` | `SaveIt` | Brand name shown on pages |
| `SITE_URL` | `http://localhost:8000` | Used for canonical URLs & sitemap |
| `RATE_LIMIT_PER_MIN` | `20` | Requests per IP per minute |
| `MAX_FILESIZE_MB` | `500` | Largest file the server will fetch |
| `COOKIES_FILE` | `/etc/secrets/cookies.txt` | Netscape cookies.txt for login-walled posts / YouTube "confirm you're not a bot". On Render: service → Environment → Secret Files → add `cookies.txt` |
| `REENCODE_MAX_SHORT_SIDE` | `1080` | AV1/VP9 videos up to this size are converted to H.264 so they play on every phone/PC |

## API

- `POST /api/info` `{ "url": "..." }` → title, uploader, and `items[]` each with `options[]` (`best`, `h1080`, `h720`, …, `audio`, `image`)
- `GET /api/download?url=...&option=best&item=0` → the file (`item` is the carousel/playlist position, 0 for single posts)

Only URLs from the five supported platforms are accepted (prevents the server being used to fetch arbitrary URLs).

## Legal

Downloading may go against the terms of service of these platforms, and content belongs to its creators. Only download content you own or have permission to use. Add your own Terms / DMCA page before launching publicly.
