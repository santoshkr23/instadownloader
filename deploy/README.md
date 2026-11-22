# Instadownloader on your Hostinger VPS

It runs on the same server as PDFTool4U without touching it:

| | PDFTool4U | Instadownloader |
|---|---|---|
| Folder | `/opt/pdftool4u` | `/opt/instadownloader` |
| Port | 80 / 443 (its Caddy) | **8010** |
| Containers | `pdftool4u`, `pdftool4u-api`, `caddy` | `instadownloader` |

Instadownloader is limited to 1.5 CPU cores and 1.5 GB RAM, so a big video conversion can't slow the PDF site down.

## Install (first time) — about 5 minutes

1. Open a terminal on the VPS (Hostinger hPanel → **VPS → Browser terminal**, or `ssh root@YOUR_VPS_IP`).
2. Paste this and press Enter:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/santoshkr23/instadownloader/main/deploy/install.sh | sudo bash
   ```
3. It asks for two passwords:
   - **Site username and password** — the whole site stays private behind these while it's not public.
   - **Admin password** — for the admin panel (offers, platforms).
4. When it finishes it prints your links:
   - Site: `http://YOUR_VPS_IP:8010`
   - Admin: `http://YOUR_VPS_IP:8010/admin`
5. If the link doesn't open: hPanel → **VPS → Firewall** → add a rule to **accept TCP port 8010**.

## Update to the latest version

Run the same command again. Your passwords, admin-panel settings and click counts are kept.

## Everyday commands

```bash
cd /opt/instadownloader
docker compose ps                     # is it running?
docker logs instadownloader --tail 50 # recent log lines (download errors show here)
docker compose restart                # restart
nano .env && docker compose up -d     # change a password or setting
```

## Where things are kept

- `/opt/instadownloader/.env` — passwords and settings
- `/opt/instadownloader-data/` — admin-panel settings (`config.json`) and offer clicks (`stats.json`). Back this folder up now and then.
- `/opt/instadownloader-secrets/cookies.txt` — optional; only if you decide to use a cookies file for YouTube or Instagram. Then `docker compose restart`.

## Going public later

1. Buy a separate domain for the downloader (not a pdftool4u.com subdomain, so a copyright complaint or ad-network problem can't spill onto the PDF site), add it to Cloudflare, and point it at the VPS IP.
2. In `/opt/instadownloader/.env`: delete the `SITE_USER` / `SITE_PASSWORD` lines and set `SITE_URL=https://yourdomain.com` and `ALLOWED_ORIGINS=https://yourdomain.com`.
3. Add the domain to PDFTool4U's Caddy so it gets HTTPS on 443 (add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `caddy` service and this block to `deploy/Caddyfile`):
   ```
   yourdomain.com {
       tls internal
       encode zstd gzip
       reverse_proxy host.docker.internal:8010
   }
   ```
4. Close port 8010 in the hPanel firewall so visitors only use the domain.

(Claude can make steps 2–3 for you when you're ready.)
