#!/usr/bin/env bash
#
# Start QMS in the foreground. Used by the systemd service and by hand.
#
cd "$(dirname "$0")" || exit 1

if [ -f qms.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./qms.env
  set +a
fi

exec ./venv/bin/uvicorn app:app \
  --host 0.0.0.0 \
  --port "${QMS_PORT:-8099}" \
  --log-level "${QMS_LOG_LEVEL:-info}"
