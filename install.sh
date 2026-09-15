#!/usr/bin/env bash
#
# QMS one-step installer — Linux AND macOS.
#
#   curl -fsSL <url>/install.sh | bash
#
# or, from a checkout:
#
#   ./install.sh
#
# What it does, in order:
#   1. finds a usable python3 (>= 3.9), installing one only if it has to
#   2. copies the app to $APP_DIR
#   3. creates a virtualenv and installs requirements
#   4. registers a service so it starts automatically
#        Linux -> systemd unit
#        macOS -> launchd LaunchAgent (starts at login, self-healing)
#   5. starts it and prints the address + a QR code you can print
#
# Everything is idempotent — safe to re-run.
#
# Options (env vars):
#   APP_DIR=/opt/qms        where to install   (macOS default: ~/QMS)
#   QMS_PORT=8099           port to listen on
#   STORE_CODE=MCSQ01       store identifier (shows on the setup page)
#   STORE_NAME="..."        store display name
#   SERVICE=system|user|launchagent|none   default: auto
#   NO_SUDO=1               never call sudo
#   NO_START=1              install the service but do not start it
#
set -euo pipefail

# ---------------------------------------------------------------- platform
case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux)  OS=linux ;;
  *)      printf '\n\033[0;31mERROR: unsupported OS: %s\033[0m\n' "$(uname -s)" >&2; exit 1 ;;
esac

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[0;32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[0;33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[0;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# When run as `curl | bash` there is no checkout, so unpack into a temp dir and
# install from there. Otherwise $SRC_DIR is the directory holding this script.
REPO_SLUG="${QMS_REPO:-mozaiz/qms-so-fulfilment}"

if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  SRC_DIR=""
fi

# `curl ... | bash` leaves no checkout to install from, so fetch one. This is
# the path store staff actually take, so it has to work with nothing but a
# terminal and an internet connection.
if [ -z "$SRC_DIR" ]; then
  printf '\n\033[1;36m==> Downloading QMS\033[0m\n'
  # Not `[ -n x ] && die` — a false test returns 1 and `set -e` kills the script.
  if ! have curl; then
    die "curl is required to download the app, and it is not installed"
  fi
  DL="$(mktemp -d)"
  curl -fsSL "https://codeload.github.com/$REPO_SLUG/tar.gz/refs/heads/main" \
    | tar xz -C "$DL" 2>/dev/null \
    || die "could not download the app — check this computer's internet connection"
  SRC_DIR="$(find "$DL" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)"
  [ -n "$SRC_DIR" ] || die "the downloaded archive was empty"
  printf '    \033[0;32m✓\033[0m downloaded %s\n' "$(basename "$SRC_DIR")"
fi

if [ "$OS" = macos ]; then
  APP_DIR="${APP_DIR:-$HOME/QMS}"
  SERVICE="${SERVICE:-auto}"
  LABEL="com.machines.qms"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
else
  APP_DIR="${APP_DIR:-/opt/qms}"
  SERVICE="${SERVICE:-auto}"
fi
QMS_PORT="${QMS_PORT:-8099}"
STORE_CODE="${STORE_CODE:-MCSQ01}"
STORE_NAME="${STORE_NAME:-Machines Store}"
NO_SUDO="${NO_SUDO:-0}"
NO_START="${NO_START:-0}"

# sudo is only ever needed on Linux, and only for the system-wide install.
SUDO=""
if [ "$OS" = linux ] && [ "$NO_SUDO" != "1" ] && have sudo && sudo -n true 2>/dev/null; then
  SUDO="sudo"
fi

# Are we installing over the directory we are running from? Then do not copy.
SELF_INSTALL=0
if [ -n "$SRC_DIR" ] && [ "$SRC_DIR" = "$APP_DIR" ]; then SELF_INSTALL=1; fi

# ---------------------------------------------------------------- 1. python
say "Checking Python (need 3.9 or newer)"

# Print "X.Y" for an interpreter, or nothing if it does not run at all.
# /usr/bin/python3 on a Mac with no Command Line Tools pops a GUI installer and
# exits nonzero — that must not kill the script.
py_ver() {
  "$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true
}

# Creating a venv needs ensurepip, NOT just the venv module. Debian/Ubuntu ship
# python3 with `import venv` succeeding while ensurepip is absent, and the venv
# creation then dies with a message pointing at the wrong fix. Probe the thing
# that actually matters.
has_ensurepip() { "$1" -c 'import ensurepip' >/dev/null 2>&1; }

PY=""
PY_V=""
PY_NEEDS=""      # right version, but venv support missing
PY_NEEDS_V=""
# Candidates in preference order. Homebrew first: it is the newest, and being on
# PATH means the user installed it deliberately.
for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  case "$cand" in /*) [ -x "$cand" ] || continue ;; *) have "$cand" || continue ;; esac
  v="$(py_ver "$cand")"
  [ -n "$v" ] || continue
  maj="${v%%.*}"; min="${v##*.}"
  if [ "$maj" -gt 3 ] || { [ "$maj" -eq 3 ] && [ "$min" -ge 9 ]; }; then
    if has_ensurepip "$cand"; then
      PY="$cand"; PY_V="$v"; break
    elif [ -z "$PY_NEEDS" ]; then
      PY_NEEDS="$cand"; PY_NEEDS_V="$v"
    fi
  fi
done

# A usable-but-incomplete interpreter: repair it rather than making the user
# work out which distro package is missing.
if [ -z "$PY" ] && [ -n "$PY_NEEDS" ]; then
  warn "$PY_NEEDS is python $PY_NEEDS_V but its venv support is incomplete — fixing"
  if have apt-get; then
    $SUDO apt-get update -qq || true
    $SUDO apt-get install -y -qq "python3${PY_NEEDS_V}-venv" 2>/dev/null \
      || $SUDO apt-get install -y -qq python3-venv 2>/dev/null || true
  elif have dnf; then
    $SUDO dnf install -y -q "python${PY_NEEDS_V}-pip" 2>/dev/null || true
  fi
  if has_ensurepip "$PY_NEEDS"; then PY="$PY_NEEDS"; PY_V="$PY_NEEDS_V"; fi
fi

if [ -z "$PY" ]; then
  if [ "$OS" = macos ]; then
    warn "no python3 found"
    if have brew; then
      ok "installing python via Homebrew"
      brew install python
      PY="$(command -v python3)"; PY_V="$(py_ver "$PY")"
    else
      # This is the single most likely macOS stumbling block, so be explicit
      # and give one clickable command rather than a paragraph.
      die "No Python 3.9+ on this Mac.

    Easiest fix — run this, click \"Install\" on the popup, then re-run me:

        xcode-select --install

    That installs Apple's Command Line Tools, which include Python 3."
    fi
  else
    warn "installing python3"
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
    PY="python3"; PY_V="$(py_ver python3)"
  fi
fi

has_ensurepip "$PY" || die "$PY (python $PY_V) cannot create a virtual environment.
    Install the venv package for it, then re-run:
        sudo apt install python3-venv      # Debian / Ubuntu
        sudo dnf install python3-pip       # Fedora / RHEL"
ok "$PY  (python $PY_V)"

# ---------------------------------------------------------------- 2. files
say "Installing to $APP_DIR"
mkdir -p "$APP_DIR"
if [ "$SELF_INSTALL" = "1" ]; then
  ok "already in place"
elif [ -z "$SRC_DIR" ]; then
  die "No checkout to install from. Run this from the unzipped QMS folder."
else
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
  ok "files copied"
fi
chmod +x "$APP_DIR/run.sh" "$APP_DIR/install.sh" 2>/dev/null || true

# ---------------------------------------------------------------- 3. venv
say "Installing Python packages"
cd "$APP_DIR"
[ -d venv ] || "$PY" -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
ok "packages installed into $APP_DIR/venv"

# ---------------------------------------------------------------- 4. config
if [ ! -f "$APP_DIR/qms.env" ]; then
  cat > "$APP_DIR/qms.env" <<ENV
# QMS configuration. Edit this file, then reload the service.
QMS_PORT=$QMS_PORT
QMS_DB="$APP_DIR/qms.db"
QMS_STORE_CODE="$STORE_CODE"
QMS_STORE_NAME="$STORE_NAME"
QMS_TZ=Asia/Kuala_Lumpur
# Scanned but no POS claimed it within this many minutes -> shows red:
QMS_STALE_MIN=15
# Claimed but not delivered to the POS within this many minutes -> shows red:
QMS_SLA_MIN=10
# Sitting at the counter, not marked complete within this many minutes -> red:
QMS_COMPLETE_MIN=10
# How many POS counters exist by default (add more later from the Setup screen):
QMS_POS_COUNT=4
ENV
  chmod 600 "$APP_DIR/qms.env"
  ok "wrote $APP_DIR/qms.env"
else
  ok "kept existing $APP_DIR/qms.env"
fi
set -a; . "$APP_DIR/qms.env"; set +a
QMS_PORT="${QMS_PORT:-8099}"

# ---------------------------------------------------------------- 5. service
say "Service"

# LaunchAgent: starts at login, self-heals on crash, and holds the Mac awake
# while it runs. caffeinate wraps the server so a POS screen never sleeps and
# drops the other counters off the LAN.
macos_plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-disu</string>
    <string>$APP_DIR/run.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$APP_DIR/qms.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/qms.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key><string>en_US.UTF-8</string>
  </dict>
</dict>
</plist>
PLIST
}

if [ "$OS" = macos ]; then
  case "$SERVICE" in
    none)
      ok "skipped — start it by hand with: $APP_DIR/run.sh"
      ;;
    *)
      SERVICE="launchagent"
      mkdir -p "$HOME/Library/LaunchAgents"
      macos_plist > "$PLIST"
      ok "wrote $PLIST"

      # bootout first so a re-run replaces cleanly instead of erroring on a
      # label that is already loaded.
      launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
      if launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null; then
        ok "launchd agent loaded (auto-starts at login)"
      elif launchctl load -w "$PLIST" 2>/dev/null; then
        ok "launchd agent loaded via legacy 'load' (auto-starts at login)"
      else
        warn "could not load the agent automatically"
        warn "load it by hand: launchctl bootstrap gui/\$UID $PLIST"
      fi
      [ "$NO_START" = "1" ] || launchctl kickstart -k "gui/$UID/$LABEL" 2>/dev/null || true
      ;;
  esac

else
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
      ok "skipped (SERVICE=$SERVICE) — start it with $APP_DIR/run.sh"
      ;;
  esac
fi

# ---------------------------------------------------------------- 6. backup
if [ "$OS" = linux ] && [ -n "$SUDO" ] && [ -d /etc/cron.daily ]; then
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
elif [ "$OS" = macos ]; then
  # launchd rather than cron: cron needs Full Disk Access on modern macOS and
  # silently does nothing without it. A LaunchAgent always works.
  BPLIST="$HOME/Library/LaunchAgents/$LABEL.backup.plist"
  cat > "$BPLIST" <<BPL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL.backup</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>mkdir -p "$APP_DIR/backups" &amp;&amp; /usr/bin/sqlite3 "$APP_DIR/qms.db" ".backup '$APP_DIR/backups/qms_$(date +%F_%H%M).db'" &amp;&amp; ls -t "$APP_DIR"/backups/qms_*.db | tail -n +31 | xargs -r rm -f</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
BPL
  launchctl bootout "gui/$UID/$LABEL.backup" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$BPLIST" 2>/dev/null \
    || launchctl load -w "$BPLIST" 2>/dev/null \
    || warn "backup agent not loaded"
  ok "nightly backup -> $APP_DIR/backups (30 days, 3:30am)"
fi

# ---------------------------------------------------------------- 7. wait + summary
if [ "$OS" = macos ]; then
  IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '')"
else
  IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi

# Wait for the port to answer rather than sleeping and hoping.
READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$QMS_PORT/api/health" >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 1
done

say "Done"
VER="$(curl -fsS --max-time 3 "http://127.0.0.1:$QMS_PORT/api/health" 2>/dev/null \
        | sed -n 's/.*"version":"\([^"]*\)".*/\1/p' || true)"
if [ "$READY" = "1" ]; then
  ok "QMS is answering on port $QMS_PORT${VER:+  (v$VER)}"
else
  warn "not answering yet on port $QMS_PORT — check the log below"
fi

cat <<SUMMARY

    Open on THIS computer :  http://localhost:$QMS_PORT
      This is a SECURE CONTEXT, so the CAMERA works here.
      Use the SCANNER role on this machine.

    Open on other devices :  http://${IP:-<this-computer-ip>}:$QMS_PORT
      No camera over plain http — those devices use Manual Entry.
      POS 1..4 and BACKSTORE are all tap-only, so this is fine.

    Store code : $STORE_CODE
    Config     : $APP_DIR/qms.env
SUMMARY

if [ "$OS" = macos ]; then
  cat <<SUMMARY

    Control the service:
      bring back up : launchctl kickstart -k gui/$UID/$LABEL
      take down     : launchctl bootout gui/$UID/$LABEL
      load again    : launchctl bootstrap gui/$UID $PLIST
      log           : tail -f $APP_DIR/qms.log

    Two things macOS may ask you about the first time:
      1. "Do you want the application python to accept incoming network
         connections?"  ->  click ALLOW, or other devices cannot reach it.
      2. "allow to find devices on local networks"  ->  allow, so the other
         counters can connect.

    The agent starts automatically when you log in. It does NOT run before
    anyone logs in, so keep automatic login on for a POS machine.
SUMMARY
else
  cat <<SUMMARY

    Check the service :  sudo systemctl status qms
    Bring it back up  :  sudo systemctl start qms
    Logs              :  journalctl -u qms -f     (add --user for a user service)
SUMMARY
fi
