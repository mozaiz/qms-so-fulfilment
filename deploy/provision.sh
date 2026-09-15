#!/usr/bin/env bash
# QMS — provision a store box. Run ONCE on the bench at HQ before shipping.
# Idempotent-ish: safe to re-run.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/qms}"
APP_USER="${APP_USER:-$USER}"
STORE_CODE="${STORE_CODE:-}"
STORE_NAME="${STORE_NAME:-Machines Store}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-}"     # e.g. qms-store12.mozaiz.my
TUNNEL_TOKEN="${TUNNEL_TOKEN:-}"           # from Cloudflare Zero Trust dashboard

echo "== QMS provisioning =="
echo "   app dir     : $APP_DIR"
echo "   store code  : $STORE_CODE"
echo "   store name  : $STORE_NAME"
echo "   hostname    : ${TUNNEL_HOSTNAME:-<none — will use quick tunnel>}"

# ---------- 1. system packages ----------
if command -v apt-get >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv python3-pip curl ca-certificates
fi

# ---------- 2. app ----------
sudo mkdir -p "$APP_DIR"
sudo chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
# copy app files (run this script from the app source dir)
rsync -a --exclude venv --exclude '*.db*' --exclude '__pycache__' \
      ./ "$APP_DIR"/

cd "$APP_DIR"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet fastapi "uvicorn[standard]" pydantic pillow

# ---------- 3. cloudflared ----------
if ! command -v cloudflared >/dev/null; then
  curl -fsSL -o /tmp/cloudflared \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  sudo install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared
fi

if [ -n "$TUNNEL_TOKEN" ]; then
  # named tunnel with a real hostname — the production path
  sudo cloudflared service install "$TUNNEL_TOKEN"
  sudo systemctl enable --now cloudflared
  echo "   cloudflared: named tunnel installed"
else
  echo "   cloudflared: no token — run manually with:"
  echo "     cloudflared tunnel --config /dev/null --url http://localhost:8099"
fi

# ---------- 4. systemd unit for the app ----------
sudo tee /etc/systemd/system/qms.service >/dev/null <<UNIT
[Unit]
Description=QMS Queue Management System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment=QMS_PORT=8099
Environment=QMS_DB=$APP_DIR/qms.db
Environment=QMS_STORE_CODE=$STORE_CODE
Environment=QMS_STORE_NAME=$STORE_NAME
Environment=QMS_TZ=Asia/Kuala_Lumpur
ExecStart=$APP_DIR/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8099 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now qms

# ---------- 5. nightly backup ----------
sudo mkdir -p /var/backups/qms
sudo tee /etc/cron.daily/qms-backup >/dev/null <<'BK'
#!/bin/sh
# SQLite online backup + 30 day retention, local only.
STAMP=$(date +%F_%H%M)
sqlite3 /opt/qms/qms.db ".backup /var/backups/qms/qms_$STAMP.db" 2>/dev/null || \
  cp /opt/qms/qms.db /var/backups/qms/qms_$STAMP.db
find /var/backups/qms -name 'qms_*.db' -mtime +30 -delete
BK
sudo chmod +x /etc/cron.daily/qms-backup

# ---------- 6. heartbeat (NO customer data — health only) ----------
if [ -n "${HEARTBEAT_URL:-}" ]; then
  sudo tee /etc/cron.hourly/qms-heartbeat >/dev/null <<HB
#!/bin/sh
curl -fsS -m 10 -X POST "$HEARTBEAT_URL" \\
  -H 'Content-Type: application/json' \\
  -d "{\\"store\\":\\"$STORE_CODE\\",\\"ts\\":\\"\$(date -Is)\\",\\"health\\":\$(curl -s -m 5 http://127.0.0.1:8099/api/health || echo '{}')}" \\
  >/dev/null 2>&1 || true
HB
  sudo chmod +x /etc/cron.hourly/qms-heartbeat
  echo "   heartbeat: enabled -> $HEARTBEAT_URL"
else
  echo "   heartbeat: skipped (set HEARTBEAT_URL to enable — strongly recommended)"
fi

echo
echo "== done =="
echo "   status : systemctl status qms"
echo "   logs   : journalctl -u qms -f"
echo "   health : curl -s localhost:8099/api/health"
echo
echo "   NEXT: scan the QR card on the box to pair the phone, then test a real barcode."
