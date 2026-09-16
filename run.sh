#!/usr/bin/env bash
#
# Start QMS in the foreground. Used by the systemd service and by hand.
#
# Two listeners, one service:
#
#   http://<ip>:8099    everything. POS and Backstore live here. Zero setup.
#   https://<ip>:8443   the scanner. A browser only hands over a camera on a
#                       secure context, and a LAN IP over plain HTTP is not one —
#                       so phones scan here, after installing the store's
#                       certificate once.
#
# Both are the same app over the same database, so both can run at once. Keeping
# them behind a single service means staff have one thing to restart, not two.
#
cd "$(dirname "$0")" || exit 1

if [ -f qms.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./qms.env
  set +a
fi

PORT="${QMS_PORT:-8099}"
TLS_PORT="${QMS_TLS_PORT:-8443}"
LEVEL="${QMS_LOG_LEVEL:-info}"
PIDS=""

cleanup() {
  if [ -n "$PIDS" ]; then
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null
  fi
  exit 0
}
trap cleanup INT TERM

# Refresh the certificate BEFORE starting. A store box on DHCP changes address,
# and a certificate that names yesterday's address fails on the phone with no
# explanation anywhere in the UI. make_cert.py keeps the same CA — so phones stay
# trusted — and prints "nothing to do" when the addresses have not moved, which
# is the normal case. Without this, the only cure is someone remembering to run
# it by hand, months later, when nobody connects the two.
if [ -f make_cert.py ] && [ -x venv/bin/python ]; then
  ./venv/bin/python make_cert.py >/dev/null 2>&1 || true
fi

# Is a port free? Portable, and no dependence on ss/lsof/netstat, which differ
# or are absent across the machines this runs on.
port_free() {
  ./venv/bin/python - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
    print("free")
except OSError:
    print("busy")
finally:
    s.close()
PY
}

TLS_STARTED=0
TLS_WANTED=0
if [ ! -f certs/server.crt ] || [ ! -f certs/server.key ]; then
  echo "no certificate in certs/ — serving plain HTTP only." >&2
  echo "run ./venv/bin/python make_cert.py to enable phone scanning." >&2
elif [ "$(port_free "$TLS_PORT")" != "free" ]; then
  # Never let the scanner port take the whole store down. Something else holds
  # it; POS and Backstore must keep working, and they are all on the plain port.
  echo "port $TLS_PORT is already in use — phone scanning is unavailable." >&2
  echo "POS and Backstore are unaffected. Check what holds it, or change" >&2
  echo "QMS_TLS_PORT in qms.env." >&2
else
  TLS_WANTED=1
fi

# Plain first, and wait for it to answer, THEN the secure one.
#
# Both listeners create and migrate the same database on a fresh install, and
# SQLite cannot serialise DDL or a journal-mode switch the way busy_timeout
# handles ordinary writes — so starting them together is a race that one of them
# loses, leaving a store quietly missing either its counters or its scanner. The
# app retries, but ordering them means there is nothing to retry.
./venv/bin/uvicorn app:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --log-level "$LEVEL" &
PIDS="$PIDS$!"

for _ in $(seq 1 40); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if [ "${TLS_WANTED:-0}" = "1" ]; then
  ./venv/bin/uvicorn app:app \
    --host 0.0.0.0 \
    --port "$TLS_PORT" \
    --ssl-keyfile certs/server.key \
    --ssl-certfile certs/server.crt \
    --log-level "$LEVEL" &
  PIDS="$PIDS $!"
  TLS_STARTED=1
fi

if [ "$TLS_STARTED" = "1" ]; then
  echo "QMS listening on http://<this-machine>:$PORT and https://<this-machine>:$TLS_PORT"
else
  echo "QMS listening on http://<this-machine>:$PORT"
fi

# macOS ships bash 3.2, which has no `wait -n`, so poll instead. If either
# listener dies, exit — the service manager restarts the whole unit rather than
# leaving a half-dead server that answers on one port and not the other.
while :; do
  for p in $PIDS; do
    kill -0 "$p" 2>/dev/null || { echo "a listener exited — stopping" >&2; cleanup; }
  done
  sleep 2
done
