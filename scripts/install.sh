#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${1:-${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if command -v id >/dev/null 2>&1; then
  CURRENT_USER="$(id -un)"
  if [ "$CURRENT_USER" != "oracle" ]; then
    echo "warning: recommended execution user is oracle; current user is $CURRENT_USER" >&2
  fi
fi

mkdir -p \
  "$APP_HOME/app/config" \
  "$APP_HOME/data" \
  "$APP_HOME/logs/jobs" \
  "$APP_HOME/backups" \
  "$APP_HOME/bundles" \
  "$APP_HOME/packages/patches" \
  "$APP_HOME/packages/releases" \
  "$APP_HOME/packages/rollback" \
  "$APP_HOME/run"

if [ "$APP_ROOT" != "$APP_HOME" ]; then
  mkdir -p "$APP_HOME/app" "$APP_HOME/scripts" "$APP_HOME/deploy"
  cp -R "$APP_ROOT/app/." "$APP_HOME/app/"
  cp -R "$APP_ROOT/scripts/." "$APP_HOME/scripts/"
  if [ -d "$APP_ROOT/deploy" ]; then cp -R "$APP_ROOT/deploy/." "$APP_HOME/deploy/"; fi
  if [ -f "$APP_ROOT/README.md" ]; then cp "$APP_ROOT/README.md" "$APP_HOME/README.md"; fi
fi

if [ ! -f "$APP_HOME/app/config/app.yaml" ]; then
  if [ -f "$APP_HOME/app/config/app.yaml.sample" ]; then
    cp "$APP_HOME/app/config/app.yaml.sample" "$APP_HOME/app/config/app.yaml"
  elif [ -f "$APP_HOME/configs/app.yaml.sample" ]; then
    cp "$APP_HOME/configs/app.yaml.sample" "$APP_HOME/app/config/app.yaml"
  else
    echo "missing app.yaml.sample" >&2
    exit 2
  fi
  echo "created $APP_HOME/app/config/app.yaml"
fi

chmod u+x "$APP_HOME/scripts"/*.sh 2>/dev/null || true
"$PYTHON_BIN" "$APP_HOME/app/oas_admin_lite.py" --config "$APP_HOME/app/config/app.yaml" --check
echo "install complete: $APP_HOME"
