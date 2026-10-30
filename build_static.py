"""Render the site to plain HTML for GitHub Pages, from data/config.json.

The pages are static; downloads and click counting are done by the FastAPI
server (on Render), whose address is given by API_BASE. The Pages workflow runs
this again whenever the admin panel saves new settings.

    API_BASE=https://instadownloader-ce1o.onrender.com BASE_PATH=/instadownloader python build_static.py
"""
import os
import shutil

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import config
from app.pages import page_context

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "dist")

BASE_PATH = os.getenv("BASE_PATH", "/instadownloader").rstrip("/")
API_BASE = os.getenv("API_BASE", "https://instadownloader-ce1o.onrender.com").rstrip("/")
SITE_URL = os.getenv("SITE_URL", f"https://santoshkr23.github.io{BASE_PATH}").rstrip("/")

env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html"]))


def ctx(key):
    return page_context(key, base=BASE_PATH, api_base=API_BASE, site_url=SITE_URL)


def write(rel, html):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    config.load(from_github=False)  # the workflow checks out the latest config.json
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(OUT, "static"))

    index = env.get_template("index.html")
    plats = config.platforms()
    write("index.html", index.render(**ctx(None)))
    for p in plats:
        write(f"{p['slug']}/index.html", index.render(**ctx(p["key"])))

    nf = ctx(None)
    nf["page_title"] = "Page not found"
    write("404.html", env.get_template("404.html").render(**nf))

    urls = [f"{SITE_URL}/"] + [f"{SITE_URL}/{p['slug']}/" for p in plats]
    write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    write(".nojekyll", "")
    print(f"Built {len(plats) + 2} pages into dist/ (base={BASE_PATH or '/'}, api={API_BASE})")


if __name__ == "__main__":
    main()
