#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG_FILE="${OAS_ADMIN_LITE_CONFIG:-$APP_HOME/app/config/app.yaml}"
PID_FILE="$APP_HOME/run/oas-admin-lite.pid"
LOG_FILE="$APP_HOME/logs/app.log"
mkdir -p "$APP_HOME/run" "$APP_HOME/logs"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "oas-admin-lite already running: $(cat "$PID_FILE")"
  exit 0
fi
cd "$APP_HOME"
nohup "$PYTHON_BIN" "$APP_HOME/app/oas_admin_lite.py" --config "$CONFIG_FILE" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "oas-admin-lite started: $(cat "$PID_FILE")"
