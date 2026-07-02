#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG_FILE="${OAS_ADMIN_LITE_CONFIG:-$APP_HOME/app/config/app.yaml}"
"$PYTHON_BIN" "$APP_HOME/app/oas_admin_lite.py" --config "$CONFIG_FILE" --check
