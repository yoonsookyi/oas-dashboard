#!/usr/bin/env bash
set -euo pipefail
VERSION="${1:-0.1.0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist"
STAGE="$DIST/oas-admin-lite-$VERSION"
rm -rf "$STAGE"
mkdir -p "$STAGE"
mkdir -p "$STAGE/app/config" "$STAGE/scripts" "$STAGE/docs"
cp -R "$ROOT/app/oas_admin_lite.py" "$STAGE/app/"
cp -R "$ROOT/app/oas_admin_lite" "$STAGE/app/"
cp "$ROOT/configs/app.yaml.sample" "$STAGE/app/config/app.yaml.sample"
for script in install.sh healthcheck.sh start.sh stop.sh status.sh update.sh rollback.sh uninstall.sh; do
  cp "$ROOT/scripts/$script" "$STAGE/scripts/"
done
cp "$ROOT/docs/DEPLOYMENT_TOPOLOGY.md" "$STAGE/docs/"
cp "$ROOT/README.md" "$STAGE/"
find "$STAGE/scripts" -type f -name "*.sh" -exec chmod u+x {} \;
tar -czf "$DIST/oas-admin-lite-$VERSION.tar.gz" -C "$DIST" "oas-admin-lite-$VERSION"
echo "$DIST/oas-admin-lite-$VERSION.tar.gz"
