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
bad()  { printf '    \033[0;31m✗\033[0m %s\n' "$*"; }
die()  { printf '\n\033[0;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# `--check` inspects the machine and stops. Nothing is downloaded, nothing is
# written, no service is touched — so it is safe to run anywhere, including on a
# machine you have not decided to deploy to yet.
CHECK_ONLY="${CHECK_ONLY:-0}"
for _a in "$@"; do
  case "$_a" in --check|-c|--dry-run) CHECK_ONLY=1 ;; esac
done

REPO_SLUG="${QMS_REPO:-mozaiz/qms-so-fulfilment}"

if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  SRC_DIR=""
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

# ================================================================ 0. PREFLIGHT
#
# Check everything this app needs and REPORT it, before a single byte is
# written or downloaded. Two reasons:
#
#   1. Someone standing at a POS counter needs to know "will this work here"
#      before committing, and needs to be told exactly what is about to be
#      downloaded on a machine that may be on a mobile hotspot.
#   2. When something is missing, the fix is a single specific command. Finding
#      that out now beats failing four minutes into an install.

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

PY=""; PY_V=""
PY_NEEDS=""; PY_NEEDS_V=""      # right version, venv support incomplete
PY_OK_LIST=""                   # report lines
PY_BAD_LIST=""
PY_SEEN=""                      # resolved paths, to dedupe

# Follow symlinks without `readlink -f`, which is a GNU extension macOS does
# not have. python3 and python3.11 are usually the same binary under two names,
# and listing it twice makes the report look broken.
resolve_py() {
  p="$1"
  case "$p" in
    /*) : ;;
    *)  p="$(command -v "$p" 2>/dev/null || echo "$p")" ;;
  esac
  n=0
  while [ -L "$p" ] && [ "$n" -lt 20 ]; do
    t="$(readlink "$p" 2>/dev/null || echo '')"
    [ -n "$t" ] || break
    case "$t" in /*) p="$t" ;; *) p="$(dirname "$p")/$t" ;; esac
    n=$((n + 1))
  done
  echo "$p"
}

choose_python() {
  # Candidates in preference order. Homebrew first: it is the newest, and being
  # on PATH means the user installed it deliberately.
  # PY_CANDIDATES overrides the list — useful when an interpreter lives
  # somewhere unusual.
  for cand in ${PY_CANDIDATES:-python3 python3.13 python3.12 python3.11 python3.10 python3.9 \
              /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3}; do
    case "$cand" in /*) [ -x "$cand" ] || continue ;; *) have "$cand" || continue ;; esac
    _rp="$(resolve_py "$cand" || true)"
    [ -n "$_rp" ] || _rp="$cand"
    case "$PY_SEEN" in *"|$_rp|"*) continue ;; esac
    PY_SEEN="$PY_SEEN|$_rp|"
    v="$(py_ver "$cand")"
    [ -n "$v" ] || continue
    maj="${v%%.*}"; min="${v##*.}"
    [ "$maj" -gt 3 ] || { [ "$maj" -eq 3 ] && [ "$min" -ge 9 ]; } || continue
    if has_ensurepip "$cand"; then
      PY_OK_LIST="$PY_OK_LIST$cand|$v|$(command -v "$cand" 2>/dev/null || echo "$cand")
"
      if [ -z "$PY" ]; then PY="$cand"; PY_V="$v"; fi
    else
      PY_BAD_LIST="$PY_BAD_LIST$cand|$v
"
      if [ -z "$PY_NEEDS" ]; then PY_NEEDS="$cand"; PY_NEEDS_V="$v"; fi
    fi
  done
}

# Can we open a TCP connection to this port? /dev/tcp is a bash builtin, present
# in the bash 3.2 that macOS still ships.
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# Is whatever holds this port actually a QMS? On a re-run it will be ours, and
# moving to a different port every upgrade would be worse than useless.
port_is_our_qms() {
  curl -fsS --max-time 2 "http://127.0.0.1:$1/api/health" 2>/dev/null | grep -q '"ok":true'
}

MACHINE_OS=""; MACHINE_CPU=""; MACHINE_RAM=""; MACHINE_DISK=""; CTL_CMD=""

gather_machine() {
  # Every substitution here ends in `|| true`. Under `set -e` an assignment
  # inherits the substitution's exit status, so one failing `df` would abort the
  # whole preflight and print nothing at all.
  MACHINE_CPU="$(uname -m 2>/dev/null || echo '?')"
  if [ "$OS" = macos ]; then
    MACHINE_OS="macOS $(sw_vers -productVersion 2>/dev/null || echo '?')"
    _b="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    if [ "$_b" -gt 0 ] 2>/dev/null; then MACHINE_RAM="$(( _b / 1048576 )) MB"; fi
    _g="$(df -g "$HOME" 2>/dev/null | awk 'NR==2{print $4}' || true)"
    if [ -n "${_g:-}" ]; then MACHINE_DISK="$_g GB"; fi
  else
    MACHINE_OS="Linux $(uname -r 2>/dev/null || echo '?')"
    _k="$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || true)"
    if [ -n "${_k:-}" ]; then MACHINE_RAM="$_k MB"; fi
    _g="$(df -BG "$HOME" 2>/dev/null | awk 'NR==2{gsub(/G/,"",$4); print $4}' || true)"
    if [ -n "${_g:-}" ]; then MACHINE_DISK="$_g GB"; fi
  fi
}

# When something is missing, this is the ONE command that fixes it.
remedy_line() {
  if [ "$OS" = macos ]; then
    if have brew; then
      printf 'Homebrew is installed, so I can fetch Python for you (about 40 MB).\n'
    else
      printf 'Run this one command, click "Install" in the window that appears,\n'
      printf 'wait for it to finish, then run me again:\n\n'
      printf '        xcode-select --install\n\n'
      printf "That installs Apple's Command Line Tools, which include Python 3.\n"
    fi
  else
    if have apt-get || have dnf || have yum; then
      printf "I can install it with this system's package manager.\n"
    else
      printf 'Install Python 3.9 or newer, then run me again.\n'
    fi
  fi
}

PREFLIGHT_READY=0

preflight() {
  gather_machine
  choose_python

  if [ "$OS" = macos ]; then CTL_CMD="launchctl"; else CTL_CMD="systemctl"; fi

  printf '\n\033[1;36m==> Checking this computer\033[0m\n\n'
  printf '    %-34s %s\n' "$MACHINE_OS" "$MACHINE_CPU"
  printf '    %-34s %s\n' "Disk free"   "${MACHINE_DISK:-?}"
  printf '    %-34s %s\n' "Memory"      "${MACHINE_RAM:-?}"

  printf '\n    \033[1mPython\033[0m (needs 3.9 or newer)\n'
  if [ -n "$PY_OK_LIST" ]; then
    printf '%s' "$PY_OK_LIST" | while IFS='|' read -r c v p; do
      [ -n "$c" ] || continue
      if [ "$c" = "$PY" ]; then
        printf '      \033[0;32m✓\033[0m %-28s %-8s \033[0;32m<- will use this\033[0m\n' "$p" "$v"
      else
        printf '      \033[0;32m✓\033[0m %-28s %s\n' "$p" "$v"
      fi
    done
  fi
  if [ -n "$PY_BAD_LIST" ]; then
    printf '%s' "$PY_BAD_LIST" | while IFS='|' read -r c v; do
      [ -n "$c" ] || continue
      printf '      \033[0;33m!\033[0m %-28s %-8s venv support incomplete\n' "$c" "$v"
    done
  fi
  if [ -z "$PY_OK_LIST" ] && [ -z "$PY_BAD_LIST" ]; then
    printf '      \033[0;31m✗\033[0m none found\n'
  fi

  # Port
  PORT_CHOSEN="$QMS_PORT"
  PORT_NOTE="free"
  if port_in_use "$QMS_PORT"; then
    if port_is_our_qms "$QMS_PORT"; then
      PORT_NOTE="in use by QMS (this is an upgrade)"
    else
      while port_in_use "$PORT_CHOSEN" && [ "$PORT_CHOSEN" -lt $((QMS_PORT + 20)) ]; do
        PORT_CHOSEN=$((PORT_CHOSEN + 1))
      done
      PORT_NOTE="taken by something else — will use $PORT_CHOSEN"
    fi
  fi

  # Existing install
  if [ -f "$APP_DIR/app.py" ]; then INSTALL_NOTE="found in $APP_DIR — will upgrade"; else INSTALL_NOTE="none — fresh install"; fi

  printf '\n    %-34s %s\n' "Port $QMS_PORT" "$PORT_NOTE"
  printf '    %-34s %s\n' "Existing install" "$INSTALL_NOTE"

  # ---- verdict
  printf '\n'
  if [ -n "$PY_OK_LIST" ]; then
    PREFLIGHT_READY=1
    printf '    \033[1;32mResult: READY\033[0m\n'
    printf '      Nothing needs installing on this computer.\n'
    printf '      It will download the app, then about 25 MB of Python packages.\n'
  elif [ -n "$PY_NEEDS" ]; then
    PREFLIGHT_READY=1
    printf '    \033[1;32mResult: READY\033[0m (after one repair)\n'
    printf '      Python %s is here but cannot make a virtual environment yet.\n' "$PY_NEEDS_V"
    printf '      I will fix that first.\n'
  else
    PREFLIGHT_READY=0
    printf '    \033[1;33mResult: ONE STEP NEEDED\033[0m\n\n'
    printf '      \033[1mMissing: Python 3.9 or newer\033[0m\n\n'
    remedy_line | sed 's/^/      /'
  fi
  printf '\n'
}

preflight

if [ "$CHECK_ONLY" = "1" ]; then
  if [ "$PREFLIGHT_READY" = "1" ]; then
    printf '    Nothing was changed. Run the same command without --check to install.\n\n'
    exit 0
  fi
  printf '    Nothing was changed. Install Python 3.9+ first, then run this again.\n\n'
  exit 1
fi

if [ "$PREFLIGHT_READY" != "1" ]; then
  die "Cannot continue without Python 3.9 or newer."
fi

QMS_PORT="$PORT_CHOSEN"

# `curl ... | bash` leaves no checkout to install from, so fetch one. This is
# the path store staff actually take, so it has to work with nothing but a
# terminal and an internet connection.
if [ -z "$SRC_DIR" ]; then
  printf '\033[1;36m==> Downloading QMS\033[0m\n'
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
  ok "downloaded $(basename "$SRC_DIR")"
fi

# Are we installing over the directory we are running from? Then do not copy.
SELF_INSTALL=0
if [ -n "$SRC_DIR" ] && [ "$SRC_DIR" = "$APP_DIR" ]; then SELF_INSTALL=1; fi

# ---------------------------------------------------------------- 1. python
say "Preparing Python"

# A usable-but-incomplete interpreter: repair it rather than making the user
# work out which distro package is missing.
if [ -z "$PY" ] && [ -n "$PY_NEEDS" ]; then
  if have brew; then
    warn "$PY_NEEDS has incomplete venv support — installing a complete Python via Homebrew"
    brew install python || true
  elif have apt-get; then
    warn "$PY_NEEDS is python $PY_NEEDS_V but its venv support is incomplete — fixing"
    $SUDO apt-get update -qq || true
    $SUDO apt-get install -y -qq "python3${PY_NEEDS_V}-venv" 2>/dev/null \
      || $SUDO apt-get install -y -qq python3-venv 2>/dev/null || true
  elif have dnf; then
    $SUDO dnf install -y -q "python${PY_NEEDS_V}-pip" 2>/dev/null || true
  fi
  # Re-scan from scratch: Homebrew may have just added a new python3.
  PY=""; PY_V=""; PY_OK_LIST=""
  choose_python
  if [ -z "$PY" ] && has_ensurepip "$PY_NEEDS"; then PY="$PY_NEEDS"; PY_V="$PY_NEEDS_V"; fi
fi

# Still nothing? Install one.
if [ -z "$PY" ]; then
  if [ "$OS" = macos ]; then
    if have brew; then
      warn "installing Python via Homebrew"
      brew install python
      PY="$(command -v python3 2>/dev/null || true)"; PY_V="$(py_ver "$PY")"
    else
      die "No Python 3.9+ on this Mac.

    Run this, click \"Install\" on the popup, then run me again:

        xcode-select --install"
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

if [ -z "$PY" ] || [ -z "$PY_V" ]; then
  die "Could not find or install a usable Python 3.9+."
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
  #
  # The work lives in its own script rather than a long inline `sh -c` string:
  # it stays readable, and it keeps the plist free of shell quoting.
  #
  # It uses the venv's Python rather than the sqlite3 command line. The python
  # is already here, whereas the sqlite3 binary is not guaranteed on Linux, and
  # `Connection.backup()` IS the online backup API — a copy taken with `cp`
  # while the app is writing gives a file that cannot be restored.
  #
  # No `xargs` either: GNU has `-r`, BSD does not, and the difference only shows
  # up on the machine you cannot easily debug.
  cat > "$APP_DIR/qms-backup.sh" <<BK
#!/bin/sh
# Nightly QMS database backup, 30 day retention. Local only.
APP_DIR="$APP_DIR"
STAMP=\$(date +%F_%H%M)
mkdir -p "\$APP_DIR/backups" || exit 1

"\$APP_DIR/venv/bin/python" - "\$APP_DIR/qms.db" "\$APP_DIR/backups/qms_\$STAMP.db" <<'PYEOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
with d:
    s.backup(d)     # consistent snapshot, safe while the server is writing
s.close()
d.close()
PYEOF

# keep the newest 30, delete anything older
ls -t "\$APP_DIR"/backups/qms_*.db 2>/dev/null | tail -n +31 | while read -r f; do
  rm -f "\$f"
done
BK
  chmod +x "$APP_DIR/qms-backup.sh"

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
    <string>$APP_DIR/qms-backup.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$APP_DIR/qms-backup.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/qms-backup.err.log</string>
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
