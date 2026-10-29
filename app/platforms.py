"""Supported platforms: hostnames, branding and SEO copy for each page."""
from urllib.parse import urlparse

PLATFORMS = {
    "instagram": {
        "name": "Instagram",
        "slug": "instagram-downloader",
        "hosts": ["instagram.com", "instagr.am"],
        "color": "#E1306C",
        "placeholder": "https://www.instagram.com/reel/...",
        "title": "Instagram Downloader – Save Reels, Videos & Photos",
        "description": "Download Instagram Reels, videos, photos and carousel posts in HD. Paste the link, pick a quality, save.",
        "what": "Reels, videos, photos and carousel posts",
    },
    "youtube": {
        "name": "YouTube",
        "slug": "youtube-downloader",
        "hosts": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
        "color": "#FF0000",
        "placeholder": "https://www.youtube.com/watch?v=...",
        "title": "YouTube Downloader – Save Videos, Shorts & MP3",
        "description": "Download YouTube videos and Shorts as MP4 up to 1080p, or extract the audio as MP3.",
        "what": "videos, Shorts and audio (MP3)",
    },
    "pinterest": {
        "name": "Pinterest",
        "slug": "pinterest-downloader",
        "hosts": ["pinterest.com", "pin.it", "pinterest.co.uk", "pinterest.ca", "pinterest.com.au", "in.pinterest.com"],
        "color": "#E60023",
        "placeholder": "https://www.pinterest.com/pin/...",
        "title": "Pinterest Downloader – Save Pin Videos & Images",
        "description": "Download Pinterest video pins and images in original quality. Works with pin.it short links.",
        "what": "video pins and images",
    },
    "twitter": {
        "name": "Twitter / X",
        "slug": "twitter-downloader",
        "hosts": ["twitter.com", "x.com", "t.co", "mobile.twitter.com"],
        "color": "#1D9BF0",
        "placeholder": "https://x.com/user/status/...",
        "title": "Twitter (X) Video Downloader – Save Tweet Videos & GIFs",
        "description": "Download videos and GIFs from Twitter / X posts in the best available quality.",
        "what": "videos and GIFs from posts",
    },
    "facebook": {
        "name": "Facebook",
        "slug": "facebook-downloader",
        "hosts": ["facebook.com", "fb.watch", "fb.com", "m.facebook.com"],
        "color": "#1877F2",
        "placeholder": "https://www.facebook.com/watch/?v=...",
        "title": "Facebook Video Downloader – Save Videos & Reels in HD",
        "description": "Download public Facebook videos and Reels in HD or SD quality.",
        "what": "public videos and Reels",
    },
}


def detect_platform(url: str) -> str | None:
    """Return the platform key for a URL, or None if it isn't a supported site."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    for key, p in PLATFORMS.items():
        for h in p["hosts"]:
            if host == h or host.endswith("." + h):
                return key
    return None
