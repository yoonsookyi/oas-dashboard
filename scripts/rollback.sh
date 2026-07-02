#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
BACKUP="${1:-}"
if [ -z "$BACKUP" ]; then
  BACKUP="$(ls -1t "$APP_HOME"/packages/rollback/app-*.tar.gz 2>/dev/null | head -1 || true)"
fi
if [ -z "$BACKUP" ] || [ ! -f "$BACKUP" ]; then
  echo "rollback archive not found" >&2
  exit 2
fi
"$APP_HOME/scripts/stop.sh" || true
tar -xzf "$BACKUP" -C "$APP_HOME"
echo "rolled back from $BACKUP"
