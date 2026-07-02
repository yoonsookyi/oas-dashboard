#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
PID_FILE="$APP_HOME/run/oas-admin-lite.pid"
if [ ! -f "$PID_FILE" ]; then
  echo "oas-admin-lite is not running"
  exit 0
fi
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "oas-admin-lite stopped: $PID"
else
  echo "stale pid file removed: $PID"
fi
rm -f "$PID_FILE"
