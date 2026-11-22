#!/usr/bin/env bash
# Instadownloader: one-command install / update for an Ubuntu VPS (Hostinger KVM etc.).
# Runs next to PDFTool4U on the same server without touching it: it uses port 8010,
# not 80/443, and never stops other containers.
#
#   curl -fsSL https://raw.githubusercontent.com/santoshkr23/instadownloader/main/deploy/install.sh | sudo bash
#
# First run: asks for passwords and saves them in /opt/instadownloader/.env.
# Later runs: get the latest code and restart. Admin settings and click counts are kept.
set -euo pipefail
DIR=/opt/instadownloader
DATA=/opt/instadownloader-data
SECRETS=/opt/instadownloader-secrets
REPO=https://github.com/santoshkr23/instadownloader.git
PORT=8010
say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Please run as root (put sudo in front)."; exit 1; }

command -v git >/dev/null || { say "Installing git"; apt-get update -y && apt-get install -y git; }
command -v docker >/dev/null || { say "Installing Docker"; curl -fsSL https://get.docker.com | sh; }
docker compose version >/dev/null 2>&1 || { say "Installing Docker Compose"; apt-get update -y && apt-get install -y docker-compose-plugin; }

if [ -d "$DIR/.git" ]; then
  say "Getting the latest code"
  git -C "$DIR" fetch --depth 1 origin main && git -C "$DIR" reset --hard origin/main
else
  say "Downloading Instadownloader"
  rm -rf "$DIR"; git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"

# admin-panel settings and click counts live outside the code folder so updates keep them
mkdir -p "$DATA" "$SECRETS"; chmod 700 "$SECRETS"
[ -f "$DATA/config.json" ] || cp data/config.json "$DATA/config.json"
[ -f "$DATA/stats.json" ] || cp data/stats.json "$DATA/stats.json"

IP=$(curl -fsS -4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')

if [ ! -f .env ]; then
  say "Passwords (you'll type these in your browser, so write them down)"
  echo "1) Site password: the whole site stays private behind this until you launch."
  read -rp "   Site username [owner]: " SU </dev/tty; SU=${SU:-owner}
  while :; do read -rsp "   Site password (10+ characters): " SP </dev/tty; echo; [ ${#SP} -ge 10 ] && break; echo "   Too short."; done
  echo "2) Admin password: for the admin panel at /admin (offers, platforms)."
  while :; do read -rsp "   Admin password (12+ characters, different): " AP </dev/tty; echo; [ ${#AP} -ge 12 ] && [ "$AP" != "$SP" ] && break; echo "   Too short or same as the site password."; done
  SK=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
  cat > .env <<EOF
# Instadownloader settings. After changing this file run:  cd $DIR && docker compose up -d
SITE_URL=http://$IP:$PORT
ALLOWED_ORIGINS=http://$IP:$PORT
PUBLIC_PORT=$PORT
# private mode: remove these two lines (or leave SITE_PASSWORD empty) when you launch
SITE_USER=$SU
SITE_PASSWORD=$SP
ADMIN_PASSWORD=$AP
SECRET_KEY=$SK
RATE_LIMIT_PER_MIN=20
MAX_FILESIZE_MB=500
EOF
  chmod 600 .env
fi
PORT=$(grep -E '^PUBLIC_PORT=' .env | cut -d= -f2 || echo 8010); PORT=${PORT:-8010}

if ss -ltn 2>/dev/null | grep -q ":$PORT " && ! docker ps --format '{{.Names}}' | grep -qx instadownloader; then
  echo "Port $PORT is already used by something else:"; ss -ltnp | grep ":$PORT "
  echo "Change PUBLIC_PORT (and the port in SITE_URL) in $DIR/.env, then run this again."; exit 1
fi
command -v ufw >/dev/null && ufw status | grep -q "Status: active" && ufw allow "$PORT"/tcp >/dev/null || true

say "Building and starting (3-5 minutes the first time)"
docker compose up -d --build
say "Waiting for it to come up"
for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi; sleep 3
done
if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  say "Done"
  echo "  Site:   http://$IP:$PORT"
  echo "  Admin:  http://$IP:$PORT/admin"
  grep -q '^SITE_PASSWORD=.\+' .env && echo "  Your browser will ask for the site username and password first."
  echo "  If it doesn't open, also allow port $PORT in Hostinger hPanel -> VPS -> Firewall."
else
  echo "It didn't start. The last log lines:"; docker logs instadownloader --tail 30
fi
