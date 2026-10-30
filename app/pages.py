"""Template context shared by the live server (main.py) and the GitHub Pages build."""
import os

from . import config

ICON_DIR = os.path.join(config.ROOT, "static", "icons")


def _has_icon(key: str) -> bool:
    return os.path.exists(os.path.join(ICON_DIR, f"{key}.svg"))


def page_context(key: str | None, *, base: str, api_base: str, site_url: str) -> dict:
    cfg = config.get()
    site_name = cfg["site_name"]
    plats = config.platform_map()
    p = plats.get(key) if key else None
    names = [x["name"] for x in plats.values() if x["mode"] == "download"]
    names_txt = ", ".join(names[:-1]) + (f" and {names[-1]}" if len(names) > 1 else (names[0] if names else ""))

    page = key or "home"
    offers = {
        "top": config.offers_for("blocked") if p and p["mode"] == "offers" else [],
        "home": config.offers_for("home") if not p else [],
        "pages": config.offers_for("pages") if p else [],
        "after_download": config.offers_for("after_download"),
    }
    # an offer already shown big at the top of a page isn't repeated lower down
    top_ids = {o["id"] for o in offers["top"]}
    offers["pages"] = [o for o in offers["pages"] if o["id"] not in top_ids]

    # what app.js needs: which links are supported, and the after-download offers
    client = {
        "api": api_base,
        "page": page,
        "platforms": [
            {"name": x["name"], "hosts": x["hosts"], "mode": x["mode"], "notice": x["notice"]}
            for x in plats.values()
        ],
        "offers": [{k: o[k] for k in ("id", "title", "text", "url", "button", "image", "badge")} for o in offers["after_download"]],
    }

    return {
        "base": base,
        "api_base": api_base,
        "site_name": site_name,
        "site_url": site_url,
        "platforms": plats,
        "icons": {k for k in plats if _has_icon(k)},
        "current": key,
        "p": p,
        "offers": offers,
        "page": page,
        "client": client,
        "supported_names": names_txt,
        "page_title": p["title"] if p else f"{site_name} – {names_txt or 'Video'} Downloader",
        "page_description": p["description"] if p else f"Free online downloader for {names_txt}. No app or signup needed.",
        "canonical": f"{site_url}/{p['slug']}/" if p else f"{site_url}/",
    }
