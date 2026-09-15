#!/usr/bin/env bash
# Regression test for install.sh — Linux path.
#
# The macOS branch cannot run launchd here, so it is exercised separately by
# test_install_macos.sh which stubs uname/launchctl/ipconfig.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"     # repo root: this script lives in deploy/
cd "$SRC" || exit 1

PASS=0; FAIL=0
chk() { if [ "$2" = "1" ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }

# The interpreter has to have ensurepip or venv creation legitimately fails.
REAL_PY_DIR="$(cd "$(dirname "$(command -v python3)")" && pwd)"

echo "============================================================"
echo "install.sh — Linux path"
echo "============================================================"

echo
echo "== syntax =="
bash -n install.sh && chk "bash -n install.sh" 1 || chk "bash -n install.sh" 0
bash -n run.sh && chk "bash -n run.sh" 1 || chk "bash -n run.sh" 0

echo
echo "== fresh install into a throwaway dir =="
D=/tmp/qms-lintest
rm -rf "$D" /tmp/qms-lintest-src
mkdir -p /tmp/qms-lintest-src
( cd "$SRC" && tar cf - --exclude=venv --exclude='*.db*' --exclude=.git \
    --exclude=screens --exclude=__pycache__ . ) | ( cd /tmp/qms-lintest-src && tar xf - )

APP_DIR="$D" QMS_PORT=8111 STORE_CODE=LIN01 STORE_NAME="Linux Test Outlet" \
  SERVICE=none NO_SUDO=1 NO_START=1 bash /tmp/qms-lintest-src/install.sh > /tmp/qms-lin.log 2>&1
chk "installer exits 0" "$([ $? -eq 0 ] && echo 1 || echo 0)"

[ -f "$D/app.py" ]                              && chk "app.py copied"            1 || chk "app.py copied"            0
[ -x "$D/run.sh" ]                              && chk "run.sh executable"        1 || chk "run.sh executable"        0
[ -x "$D/venv/bin/python" ]                     && chk "venv created"             1 || chk "venv created"             0
[ -f "$D/qms.env" ]                             && chk "qms.env written"          1 || chk "qms.env written"          0
[ -f "$D/static/js/app.js" ]                    && chk "static/ copied"           1 || chk "static/ copied"           0
stat -c '%a' "$D/qms.env" 2>/dev/null | grep -q '^600$' \
  && chk "qms.env is chmod 600" 1 || chk "qms.env is chmod 600" 0

echo
echo "== generated qms.env =="
# shellcheck disable=SC1090
set -a; . "$D/qms.env"; set +a
[ "$QMS_STORE_CODE" = "LIN01" ]                 && chk "STORE_CODE persisted (values with spaces survive)" 1 || chk "STORE_CODE persisted" 0
[ "$QMS_STORE_NAME" = "Linux Test Outlet" ]     && chk "STORE_NAME with spaces intact"                     1 || chk "STORE_NAME with spaces intact" 0
[ "${QMS_COMPLETE_MIN:-}" = "10" ]              && chk "QMS_COMPLETE_MIN present"                           1 || chk "QMS_COMPLETE_MIN present" 0
grep -q 'QMS_COMPLETE_MIN' "$D/qms.env"         && chk "QMS_COMPLETE_MIN in the file"                       1 || chk "QMS_COMPLETE_MIN in the file" 0

echo
echo "== running the installed app against the full test suite =="
cd "$D" || exit 1
QMS_PORT=8111 QMS_DB="$D/qms.db" QMS_STORE_CODE=LIN01 QMS_STORE_NAME="Linux Test Outlet" \
  ./venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8111 --log-level warning \
  > "$D/srv.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT

for _ in $(seq 1 25); do
  curl -fsS --max-time 2 http://127.0.0.1:8111/api/health >/dev/null 2>&1 && break
  sleep 1
done
HEALTH="$(curl -fsS --max-time 3 http://127.0.0.1:8111/api/health 2>/dev/null)"
echo "$HEALTH" | grep -q '"ok":true' && chk "installed instance is healthy" 1 || chk "installed instance is healthy" 0
echo "        $HEALTH"

./venv/bin/python seed_demo.py >/dev/null 2>&1
OUT="$(./venv/bin/python "$SRC/test_flow.py" http://127.0.0.1:8111 2>&1 | tail -4)"
echo "$OUT" | grep -q 'ALL GREEN' && chk "test_flow.py ALL GREEN against the installed copy" 1 \
                                 || chk "test_flow.py ALL GREEN against the installed copy" 0
echo "        $(echo "$OUT" | grep -o 'PASSED [0-9]* / [0-9]*')"

kill $SRV 2>/dev/null; wait $SRV 2>/dev/null

echo
echo "== --check: inspect without changing anything =="
env -i PATH="$REAL_PY_DIR:/usr/local/bin:/usr/bin:/bin" HOME=/tmp \
  bash /tmp/qms-lintest-src/install.sh --check > /tmp/qms-lin-check.log 2>&1
chk "--check exits 0 on a capable machine" "$([ $? -eq 0 ] && echo 1 || echo 0)"
grep -q 'Result: READY'       /tmp/qms-lin-check.log && chk "reports READY" 1 || chk "reports READY" 0
grep -q 'Nothing was changed' /tmp/qms-lin-check.log && chk "changes nothing" 1 || chk "changes nothing" 0

env -i PATH="/usr/bin:/bin" HOME=/tmp PY_CANDIDATES=/nonexistent/python3 \
  bash /tmp/qms-lintest-src/install.sh --check > /tmp/qms-lin-no.log 2>&1
chk "--check with no Python exits nonzero" "$([ $? -ne 0 ] && echo 1 || echo 0)"
grep -q 'ONE STEP NEEDED' /tmp/qms-lin-no.log && chk "says ONE STEP NEEDED" 1 || chk "says ONE STEP NEEDED" 0
grep -q "package manager" /tmp/qms-lin-no.log && chk "offers the Linux fix" 1 || chk "offers the Linux fix" 0

echo
echo "============================================================"
echo "PASSED $PASS / $((PASS+FAIL))"
[ "$FAIL" -eq 0 ] && echo "ALL GREEN" || echo "FAILURES: $FAIL"
echo "============================================================"
[ "$FAIL" -eq 0 ]
