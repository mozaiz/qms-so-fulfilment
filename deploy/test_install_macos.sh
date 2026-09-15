#!/usr/bin/env bash
# Exercise the macOS branch of install.sh on a Linux box.
#
# launchd itself cannot run here, so uname/launchctl/ipconfig/caffeinate are
# stubbed and their calls recorded. That covers the parts that actually break in
# practice — interpreter discovery, plist generation, path quoting, config
# writing — and leaves only "does launchd accept a well-formed plist", which is
# checked structurally with plistlib.
#
# The install path deliberately contains a space, because macOS home directories
# often do and an unquoted path breaks on source.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SRC" || exit 1

PASS=0; FAIL=0
chk() { if [ "$2" = "1" ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }

FAKE_HOME="/tmp/qms mac home"
APP="/tmp/qms mac app copy"
STUB="/tmp/qms-mac-stubs"
CALLS="/tmp/qms-launchctl.calls"

echo "============================================================"
echo "install.sh — macOS branch (stubbed launchd)"
echo "============================================================"

rm -rf "$FAKE_HOME" "$APP" "$STUB" "$CALLS" /tmp/qms-mac-src
mkdir -p "$FAKE_HOME" "$STUB" "/tmp/qms-mac-src"

# --- stubs ---------------------------------------------------------------
cat > "$STUB/uname" <<'EOF'
#!/bin/sh
# Only lie about the kernel; everything else delegates to the real uname.
[ "$1" = "-s" ] && { echo Darwin; exit 0; }
exec /usr/bin/uname "$@"
EOF

cat > "$STUB/launchctl" <<EOF
#!/bin/sh
echo "\$@" >> "$CALLS"
exit 0
EOF

cat > "$STUB/ipconfig" <<'EOF'
#!/bin/sh
[ "$1" = "getifaddr" ] && { echo 10.9.8.77; exit 0; }
exit 1
EOF

cat > "$STUB/caffeinate" <<'EOF'
#!/bin/sh
shift  # drop the flags
exec "$@"
EOF
chmod +x "$STUB"/*

# --- run -----------------------------------------------------------------
( cd "$SRC" && tar cf - --exclude=venv --exclude='*.db*' --exclude=.git \
    --exclude=screens --exclude=__pycache__ . ) | ( cd /tmp/qms-mac-src && tar xf - )

# The interpreter has to have ensurepip or venv creation legitimately fails;
# pick the one this box actually uses.
REAL_PY_DIR="$(cd "$(dirname "$(command -v python3)")" && pwd)"
echo "        using python from $REAL_PY_DIR"

echo
echo "== installer run (APP_DIR contains a space, HOME is fake) =="
env -i \
  PATH="$STUB:$REAL_PY_DIR:/usr/local/bin:/usr/bin:/bin" \
  HOME="$FAKE_HOME" \
  APP_DIR="$APP" \
  QMS_PORT=8123 \
  STORE_CODE="MAC01" \
  STORE_NAME="MacBook Air Test" \
  SERVICE=launchagent \
  NO_START=1 \
  bash /tmp/qms-mac-src/install.sh > /tmp/qms-mac.log 2>&1
RC=$?
chk "installer exits 0   (rc=$RC)" "$([ $RC -eq 0 ] && echo 1 || echo 0)"

PLIST="$FAKE_HOME/Library/LaunchAgents/com.machines.qms.plist"
BKPLIST="$FAKE_HOME/Library/LaunchAgents/com.machines.qms.backup.plist"

echo
echo "== files =="
[ -f "$APP/app.py" ]                     && chk "app.py installed"                 1 || chk "app.py installed"                 0
# Not just "the file exists" — actually run it, or a broken venv passes.
"$APP/venv/bin/python" -c 'import fastapi, uvicorn' 2>/dev/null \
  && chk "venv created and imports fastapi+uvicorn" 1 || chk "venv created and imports fastapi+uvicorn" 0
[ -f "$APP/qms.env" ]                    && chk "qms.env written"                  1 || chk "qms.env written"                  0
[ -f "$PLIST" ]                          && chk "LaunchAgent plist written"        1 || chk "LaunchAgent plist written"        0
[ -f "$BKPLIST" ]                        && chk "backup LaunchAgent written"       1 || chk "backup LaunchAgent written"       0

echo
echo "== launchctl was actually driven =="
grep -q 'bootstrap' "$CALLS" 2>/dev/null && chk "launchctl bootstrap called" 1 || chk "launchctl bootstrap called" 0
grep -q 'bootout'   "$CALLS" 2>/dev/null && chk "launchctl bootout first (idempotent re-run)" 1 || chk "launchctl bootout first" 0
echo "        calls: $(tr '\n' '|' < "$CALLS" 2>/dev/null)"

echo
echo "== qms.env survives a path with spaces =="
set -a
# shellcheck disable=SC1090
. "$APP/qms.env" 2>/dev/null
set +a
[ "$QMS_DB" = "$APP/qms.db" ]            && chk "QMS_DB parses with a space in the path" 1 || chk "QMS_DB parses" 0
echo "        QMS_DB=$QMS_DB"
[ "$QMS_STORE_NAME" = "MacBook Air Test" ] && chk "STORE_NAME with spaces intact" 1 || chk "STORE_NAME with spaces intact" 0

echo
echo "== plists are structurally valid (plistlib) =="
python3 - "$PLIST" "$BKPLIST" "$APP" <<'PY'
import plistlib, sys, os, re
main, bk, app = sys.argv[1], sys.argv[2], sys.argv[3]
fail = 0

with open(main, "rb") as f:
    p = plistlib.load(f)
args = p.get("ProgramArguments", [])

def ck(name, cond):
    global fail
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond: fail += 1

ck("plist parses as XML plist", True)
ck("Label is com.machines.qms", p.get("Label") == "com.machines.qms")
ck("starts at login (RunAtLoad)", p.get("RunAtLoad") is True)
ck("restarts on crash (KeepAlive)", p.get("KeepAlive") is True)
ck("wrapped in caffeinate so the Mac stays awake", args[:1] == ["/usr/bin/caffeinate"])
ck("passes the sleep-prevention flags", len(args) > 1 and args[1].startswith("-"))
ck("runs run.sh from the install dir", args[-1] == os.path.join(app, "run.sh"))
ck("run.sh actually exists and is executable", os.access(args[-1], os.X_OK))
ck("WorkingDirectory is the install dir", p.get("WorkingDirectory") == app)
ck("logs to the install dir", p.get("StandardOutPath") == os.path.join(app, "qms.log"))

with open(bk, "rb") as f:
    b = plistlib.load(f)
bkargs = b.get("ProgramArguments", [])
ck("backup agent has a daily schedule", b.get("StartCalendarInterval") == {"Hour": 3, "Minute": 30})
ck("backup agent runs the backup script", bool(bkargs) and bkargs[-1].endswith("qms-backup.sh"))

with open(os.path.join(app, "qms-backup.sh")) as f:
    bks = f.read()
ck("backup uses sqlite's online backup API, not cp", ".backup(" in bks)
ck("backup has a retention window", "tail -n +31" in bks)
# The script expands $APP_DIR rather than baking in a literal path, so check
# that every expansion is quoted. The behavioural proof is above: the whole
# suite runs with a space in the install path and the backup file appears.
ck("every $APP_DIR expansion in the backup script is quoted",
   not re.search(r'(?<!")[$]APP_DIR', bks))
sys.exit(1 if fail else 0)
PY
[ $? -eq 0 ] && chk "plist assertions all passed" 1 || chk "plist assertions all passed" 0

echo
echo "== the installed app boots and works on this copy =="
cd "$APP" || exit 1
QMS_PORT=8123 QMS_DB="$APP/qms.db" QMS_STORE_CODE=MAC01 QMS_STORE_NAME="MacBook Air Test" \
  ./venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8123 --log-level warning \
  > "$APP/srv.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for _ in $(seq 1 25); do
  curl -fsS --max-time 2 http://127.0.0.1:8123/api/health >/dev/null 2>&1 && break
  sleep 1
done
H="$(curl -fsS --max-time 3 http://127.0.0.1:8123/api/health 2>/dev/null)"
echo "$H" | grep -q '"ok":true' && chk "instance healthy" 1 || chk "instance healthy" 0
echo "        $H"
./venv/bin/python seed_demo.py >/dev/null 2>&1
O="$(./venv/bin/python "$SRC/test_flow.py" http://127.0.0.1:8123 2>&1 | tail -3)"
echo "$O" | grep -q 'ALL GREEN' && chk "test_flow.py ALL GREEN" 1 || chk "test_flow.py ALL GREEN" 0
echo "        $(echo "$O" | grep -o 'PASSED [0-9]* / [0-9]*')"
kill $SRV 2>/dev/null; wait $SRV 2>/dev/null

echo
echo "== summary printed to the operator =="
grep -q 'launchctl kickstart -k' /tmp/qms-mac.log && chk "tells the operator how to restart" 1 || chk "tells the operator how to restart" 0
grep -q 'accept incoming network'  /tmp/qms-mac.log && chk "warns about the macOS firewall prompt" 1 || chk "warns about the macOS firewall prompt" 0
grep -q 'localhost'                /tmp/qms-mac.log && chk "prints the localhost address" 1 || chk "prints the localhost address" 0
grep -q '10.9.8.77'                /tmp/qms-mac.log && chk "prints the LAN address from ipconfig" 1 || chk "prints the LAN address" 0

echo
echo "== the backup job actually runs, and is portable =="
[ -f "$APP/qms-backup.sh" ] && chk "backup script written" 1 || chk "backup script written" 0
sh -n "$APP/qms-backup.sh" 2>/dev/null && chk "backup script is valid sh" 1 || chk "backup script is valid sh" 0
# `xargs -r` is a GNU extension; BSD xargs on macOS rejects it outright.
# strip comments first: prose about xargs is not a call to xargs
if grep -v '^[[:space:]]*#' "$APP/qms-backup.sh" 2>/dev/null | grep -q 'xargs'; then
  chk "no GNU-only xargs in the backup" 0
else
  chk "no GNU-only xargs in the backup" 1
fi
"$APP/venv/bin/python" -c "import sqlite3;c=sqlite3.connect('$APP/qms.db');c.execute('create table if not exists t(x)');c.commit()" 2>/dev/null
sh "$APP/qms-backup.sh" >/dev/null 2>&1
if ls "$APP"/backups/qms_*.db >/dev/null 2>&1; then
  chk "backup script produced a backup file" 1
else
  chk "backup script produced a backup file" 0
fi
# run it 3x: retention must not eat itself
sh "$APP/qms-backup.sh" >/dev/null 2>&1; sh "$APP/qms-backup.sh" >/dev/null 2>&1
_n="$(ls "$APP"/backups/qms_*.db 2>/dev/null | wc -l | tr -d ' ')"
[ "$_n" -ge 1 ] && chk "re-running the backup keeps the latest ($_n files)" 1 \
                || chk "re-running the backup keeps the latest" 0

echo
echo "== --check: inspect a machine without changing anything =="
env -i PATH="$STUB:$REAL_PY_DIR:/usr/local/bin:/usr/bin:/bin" HOME="$FAKE_HOME" \
  APP_DIR="$APP" QMS_PORT=8123 bash /tmp/qms-mac-src/install.sh --check > /tmp/qms-check-ok.log 2>&1
chk "--check on a capable Mac exits 0" "$([ $? -eq 0 ] && echo 1 || echo 0)"
grep -q 'Result: READY'      /tmp/qms-check-ok.log && chk "--check reports READY" 1 || chk "--check reports READY" 0
grep -q 'Nothing was changed' /tmp/qms-check-ok.log && chk "--check changes nothing" 1 || chk "--check changes nothing" 0
grep -q 'Disk free'          /tmp/qms-check-ok.log && chk "--check reports disk + memory" 1 || chk "--check reports disk + memory" 0

# No python, no homebrew, no package manager — the exact fix must be printed.
env -i PATH="$STUB:/usr/bin:/bin" HOME="$FAKE_HOME" PY_CANDIDATES=/nonexistent/python3 \
  bash /tmp/qms-mac-src/install.sh --check > /tmp/qms-check-no.log 2>&1
chk "--check with no Python exits nonzero" "$([ $? -ne 0 ] && echo 1 || echo 0)"
grep -q 'ONE STEP NEEDED'       /tmp/qms-check-no.log && chk "says ONE STEP NEEDED" 1 || chk "says ONE STEP NEEDED" 0
grep -q 'xcode-select --install' /tmp/qms-check-no.log && chk "prints the exact macOS fix" 1 || chk "prints the exact macOS fix" 0
grep -q 'Python 3.9 or newer'   /tmp/qms-check-no.log && chk "names what is missing" 1 || chk "names what is missing" 0

echo
echo "============================================================"
echo "PASSED $PASS / $((PASS+FAIL))"
[ "$FAIL" -eq 0 ] && echo "ALL GREEN" || echo "FAILURES: $FAIL"
echo "============================================================"
[ "$FAIL" -eq 0 ]
