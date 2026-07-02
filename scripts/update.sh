#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
RELEASE_TAR="${1:-}"
if [ -z "$RELEASE_TAR" ]; then
  echo "usage: $0 /path/to/oas-admin-lite-release.tar.gz" >&2
  exit 2
fi
if [ ! -f "$RELEASE_TAR" ]; then
  echo "release tar not found: $RELEASE_TAR" >&2
  exit 2
fi
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$APP_HOME/packages/rollback"
if [ -d "$APP_HOME/app" ]; then
  tar -czf "$APP_HOME/packages/rollback/app-$TS.tar.gz" -C "$APP_HOME" app
fi
tar -xzf "$RELEASE_TAR" -C "$APP_HOME"
echo "updated from $RELEASE_TAR"
