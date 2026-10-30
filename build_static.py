"""Render the site to plain HTML for GitHub Pages.

The pages are static; downloads are done by the FastAPI server (e.g. on Render),
whose address is given by API_BASE.

    API_BASE=https://instadownloader-ce1o.onrender.com BASE_PATH=/instadownloader python build_static.py
"""
import os
import shutil

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.platforms import PLATFORMS

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "dist")

SITE_NAME = os.getenv("SITE_NAME", "SaveIt")
BASE_PATH = os.getenv("BASE_PATH", "/instadownloader").rstrip("/")
API_BASE = os.getenv("API_BASE", "https://instadownloader-ce1o.onrender.com").rstrip("/")
SITE_URL = os.getenv("SITE_URL", f"https://santoshkr23.github.io{BASE_PATH}").rstrip("/")

env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html"]))


def ctx(key):
    p = PLATFORMS.get(key) if key else None
    return {
        "site_name": SITE_NAME,
        "site_url": SITE_URL,
        "platforms": PLATFORMS,
        "current": key,
        "p": p,
        "base": BASE_PATH,
        "api_base": API_BASE,
        "page_title": p["title"] if p else f"{SITE_NAME} – Instagram, YouTube, Pinterest, Twitter & Facebook Downloader",
        "page_description": p["description"] if p else "Free online downloader for Instagram Reels, YouTube videos, Pinterest pins, Twitter/X videos and Facebook videos. No app or signup needed.",
        "canonical": f"{SITE_URL}/{p['slug']}/" if p else f"{SITE_URL}/",
    }


def write(rel, html):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(OUT, "static"))

    index = env.get_template("index.html")
    write("index.html", index.render(**ctx(None)))
    for key, p in PLATFORMS.items():
        write(f"{p['slug']}/index.html", index.render(**ctx(key)))

    nf = ctx(None)
    nf["page_title"] = "Page not found"
    write("404.html", env.get_template("404.html").render(**nf))

    urls = [f"{SITE_URL}/"] + [f"{SITE_URL}/{p['slug']}/" for p in PLATFORMS.values()]
    write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    write(".nojekyll", "")
    print(f"Built {len(PLATFORMS) + 2} pages into dist/ (base={BASE_PATH or '/'}, api={API_BASE})")


if __name__ == "__main__":
    main()
