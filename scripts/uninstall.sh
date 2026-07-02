#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
KEEP_DATA="${KEEP_DATA:-1}"
"$APP_HOME/scripts/stop.sh" || true
rm -rf "$APP_HOME/app" "$APP_HOME/scripts" "$APP_HOME/deploy" "$APP_HOME/run"
if [ "$KEEP_DATA" = "0" ]; then
  rm -rf "$APP_HOME/data" "$APP_HOME/logs" "$APP_HOME/backups" "$APP_HOME/bundles" "$APP_HOME/packages"
fi
echo "uninstalled app files from $APP_HOME"
