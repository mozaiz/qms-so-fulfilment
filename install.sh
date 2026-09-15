#!/usr/bin/env bash
#
# QMS one-step installer (Linux).
#
#   curl -fsSL <url>/install.sh | bash
#
# or, from a checkout:
#
#   ./install.sh
#
# What it does, in order:
#   1. installs python3 + venv if missing
#   2. copies the app to $APP_DIR  (default /opt/qms)
#   3. creates a virtualenv and installs requirements
#   4. writes a systemd service so it starts on boot
#   5. starts it and prints the address + a QR code you can print
#
# Everything is idempotent — safe to re-run.
#
# Options (env vars):
#   APP_DIR=/opt/qms        where to install
#   QMS_PORT=8099           port to listen on
#   STORE_CODE=MCSQ01       store identifier (shows on the setup page)
#   STORE_NAME="..."        store display name
#   SERVICE=system|user|none   default: system when sudo is available, else user
#   NO_SUDO=1               never call sudo
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-/opt/qms}"
QMS_PORT="${QMS_PORT:-8099}"
STORE_CODE="${STORE_CODE:-MCSQ01}"
STORE_NAME="${STORE_NAME:-Machines Store}"
SERVICE="${SERVICE:-auto}"
NO_SUDO="${NO_SUDO:-0}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[0;32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[0;33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[0;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

SUDO=""
if [ "$NO_SUDO" != "1" ] && have sudo && sudo -n true 2>/dev/null; then
  SUDO="sudo"
elif [ "$SERVICE" = "system" ]; then
  warn "sudo is not available without a password — falling back to a user service"
  SERVICE="user"
fi

# ---------------------------------------------------------------- 1. python
say "Checking Python"
if have python3 && python3 -c 'import venv' 2>/dev/null; then
  ok "python3 $(python3 -V 2>&1 | awk '{print $2}')"
else
  warn "installing python3 + venv"
  if have apt-get; then
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq python3 python3-venv python3-pip ca-certificates curl
  elif have dnf; then
    $SUDO dnf install -y -q python3 python3-pip
  elif have yum; then
    $SUDO yum install -y -q python3 python3-pip
  else
    die "No supported package manager. Install python3 + venv manually."
  fi
  ok "python3 installed"
fi

# ---------------------------------------------------------------- 2. files
say "Installing to $APP_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
  $SUDO mkdir -p "$APP_DIR"
  $SUDO chown -R "$(id -u):$(id -g)" "$APP_DIR" 2>/dev/null || true
  if have rsync; then
    rsync -a --delete \
      --exclude venv --exclude '*.db*' \
      --exclude __pycache__ --exclude .git --exclude screens \
      --exclude 'test_barcodes.png' \
      "$SRC_DIR"/ "$APP_DIR"/
  else
    for f in app.py requirements.txt run.sh install.sh; do
      [ -f "$SRC_DIR/$f" ] && cp "$SRC_DIR/$f" "$APP_DIR"/
    done
    cp -r "$SRC_DIR/static" "$APP_DIR"/
    cp -r "$SRC_DIR/deploy" "$APP_DIR"/ 2>/dev/null || true
  fi
fi
ok "files in place"

# ---------------------------------------------------------------- 3. venv
say "Installing Python packages"
cd "$APP_DIR"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
ok "packages installed"

# ---------------------------------------------------------------- 4. config
if [ ! -f "$APP_DIR/qms.env" ]; then
  cat > "$APP_DIR/qms.env" <<ENV
# QMS configuration — edit then: sudo systemctl restart qms
QMS_PORT=$QMS_PORT
QMS_DB=$APP_DIR/qms.db
QMS_STORE_CODE="$STORE_CODE"
QMS_STORE_NAME="$STORE_NAME"
QMS_TZ=Asia/Kuala_Lumpur
# An SO scanned but not claimed by any POS for this many minutes shows red:
QMS_STALE_MIN=15
# A claimed SO not delivered within this many minutes shows red:
QMS_SLA_MIN=10
# How many POS counters exist by default (add more later from the Setup screen):
QMS_POS_COUNT=4
ENV
  chmod 600 "$APP_DIR/qms.env"
  ok "wrote $APP_DIR/qms.env"
else
  ok "kept existing $APP_DIR/qms.env"
fi
set -a; . "$APP_DIR/qms.env"; set +a

# ---------------------------------------------------------------- 5. service
say "Service"
UNIT_CONTENT="[Unit]
Description=QMS - SO Fulfilment
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/qms.env
ExecStart=$APP_DIR/venv/bin/uvicorn app:app --host 0.0.0.0 --port \${QMS_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"

if [ "$SERVICE" = "auto" ]; then
  if [ -n "$SUDO" ]; then SERVICE="system"; else SERVICE="user"; fi
fi

case "$SERVICE" in
  system)
    printf '%s\n' "$UNIT_CONTENT" | $SUDO tee /etc/systemd/system/qms.service >/dev/null
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --now qms
    ok "systemd service 'qms' enabled and started"
    ;;
  user)
    mkdir -p ~/.config/systemd/user
    UNIT_USER="$(printf '%s\n' "$UNIT_CONTENT" | sed 's/WantedBy=multi-user.target/WantedBy=default.target/')"
    printf '%s\n' "$UNIT_USER" > ~/.config/systemd/user/qms.service
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now qms 2>/dev/null || warn "start it manually: ./run.sh"
    ok "user service 'qms' installed"
    ;;
  *)
    ok "skipped (SERVICE=none) — start it with ./run.sh"
    ;;
esac

# ---------------------------------------------------------------- 6. backup
if [ -n "$SUDO" ] && [ -d /etc/cron.daily ]; then
  $SUDO tee /etc/cron.daily/qms-backup >/dev/null <<BK
#!/bin/sh
# SQLite online backup, 30 day retention. Local only.
STAMP=\$(date +%F_%H%M)
mkdir -p /var/backups/qms
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$APP_DIR/qms.db" ".backup /var/backups/qms/qms_\$STAMP.db"
else
  cp "$APP_DIR/qms.db" "/var/backups/qms/qms_\$STAMP.db"
fi
find /var/backups/qms -name 'qms_*.db' -mtime +30 -delete
BK
  $SUDO chmod +x /etc/cron.daily/qms-backup
  ok "nightly backup -> /var/backups/qms (30 days)"
fi

# ---------------------------------------------------------------- 7. summary
sleep 2
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done"
cat <<SUMMARY
    Open on THIS computer :  http://localhost:$QMS_PORT
      (this is a secure context, so the camera works here)

    Open on other devices :  http://${IP:-<this-box-ip>}:$QMS_PORT
      (no camera over plain http - use Manual Entry there, or the Scanner
       role on the box itself)

    Setup page, address QR :  http://${IP:-<this-box-ip>}:$QMS_PORT  ->  ⚙ Setup

    Store code : $STORE_CODE
    Config     : $APP_DIR/qms.env     (edit, then restart the service)
    Logs       : journalctl -u qms -f     (add --user for a user service)
SUMMARY
