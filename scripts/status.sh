#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
PID_FILE="$APP_HOME/run/oas-admin-lite.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "RUNNING $(cat "$PID_FILE")"
else
  echo "STOPPED"
fi
