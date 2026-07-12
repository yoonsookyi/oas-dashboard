#!/usr/bin/env bash
set -u

APP_HOME="${OAS_ADMIN_LITE_HOME:-/u01/oas-admin-lite}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG_FILE="${OAS_ADMIN_LITE_CONFIG:-$APP_HOME/app/config/app.yaml}"
ENTRYPOINT="$APP_HOME/app/oas_admin_lite.py"
FAILURES=0
WARNINGS=0

section() {
  printf '\n== %s ==\n' "$1"
}

ok() {
  printf '[OK] %s\n' "$1"
}

warn() {
  WARNINGS=$((WARNINGS + 1))
  printf '[WARN] %s\n' "$1"
}

fail() {
  FAILURES=$((FAILURES + 1))
  printf '[FAIL] %s\n' "$1"
}

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    ok "command available: $1 ($(command -v "$1"))"
  else
    fail "required command not found: $1"
  fi
}

section "App runtime"
check_command bash
check_command tar
check_command gzip
check_command mkdir
check_command chmod
check_command nohup

if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  ok "python command available: $PYTHON_BIN ($(command -v "$PYTHON_BIN"))"
else
  fail "python command not found: $PYTHON_BIN"
fi

if command -v id >/dev/null 2>&1; then
  CURRENT_USER="$(id -un 2>/dev/null || true)"
  if [ "$CURRENT_USER" = "oracle" ]; then
    ok "execution user: oracle"
  else
    warn "recommended execution user is oracle; current user is ${CURRENT_USER:-unknown}"
  fi
else
  warn "id command not available; cannot verify execution user"
fi

if [ "$(uname -s 2>/dev/null || echo unknown)" = "Linux" ]; then
  ok "runtime OS: Linux"
else
  warn "runtime OS is not Linux; customer deployment target is Linux"
fi

if [ -f "$ENTRYPOINT" ]; then
  ok "app entrypoint exists: $ENTRYPOINT"
else
  fail "app entrypoint not found: $ENTRYPOINT"
fi

if [ -f "$CONFIG_FILE" ]; then
  ok "config file exists: $CONFIG_FILE"
else
  fail "config file not found: $CONFIG_FILE"
fi

section "App initialization"
if [ -f "$ENTRYPOINT" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if "$PYTHON_BIN" "$ENTRYPOINT" --config "$CONFIG_FILE" --check; then
    ok "app config loaded and local app directories initialized"
  else
    fail "app --check failed; review config paths and permissions"
  fi
else
  fail "skipped app --check because python or entrypoint is missing"
fi

section "Environment and OAS/OHS prerequisites"
if [ -f "$ENTRYPOINT" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  "$PYTHON_BIN" - "$APP_HOME" "$CONFIG_FILE" <<'PY'
import importlib
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

app_home = sys.argv[1]
config_file = sys.argv[2]
failures = 0
warnings = 0

def line(status, message):
    print("[{0}] {1}".format(status, message))

def ok(message):
    line("OK", message)

def warn(message):
    global warnings
    warnings += 1
    line("WARN", message)

def fail(message):
    global failures
    failures += 1
    line("FAIL", message)

def check_path(label, value, kind="path", required=True, executable=False, writable=False):
    value = str(value or "").strip()
    report_problem = fail if required else warn
    if not value:
        report_problem("{0} is not configured".format(label))
        return
    path = Path(value)
    if not path.exists():
        report_problem("{0} not found: {1}".format(label, value))
        return
    if kind == "dir" and not path.is_dir():
        report_problem("{0} is not a directory: {1}".format(label, value))
        return
    if kind == "file" and not path.is_file():
        report_problem("{0} is not a file: {1}".format(label, value))
        return
    if not os.access(str(path), os.R_OK):
        report_problem("{0} is not readable by current user: {1}".format(label, value))
        return
    if executable and not os.access(str(path), os.X_OK):
        report_problem("{0} is not executable/searchable by current user: {1}".format(label, value))
        return
    if writable and not os.access(str(path), os.W_OK):
        report_problem("{0} is not writable by current user: {1}".format(label, value))
        return
    ok("{0}: {1}".format(label, value))

def split_listen(value):
    value = str(value or "127.0.0.1:18080")
    if ":" not in value:
        return value, 18080
    host, port = value.rsplit(":", 1)
    return host, int(port)

def check_tcp_connect(label, host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            ok("{0} is listening on {1}:{2}".format(label, host, port))
    except Exception as exc:
        warn("{0} is not listening on {1}:{2}: {3}".format(label, host, port, exc))

def check_bind_available(label, host, port):
    bind_host = host
    if host in ("0.0.0.0", ""):
        bind_host = "0.0.0.0"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, int(port)))
        sock.close()
        ok("{0} listen address is available: {1}:{2}".format(label, host, port))
    except Exception as exc:
        warn("{0} listen address may already be in use: {1}:{2}: {3}".format(label, host, port, exc))

if sys.version_info >= (3, 6):
    ok("python version: {0}".format(sys.version.split()[0]))
else:
    fail("python 3.6 or newer is required; current version is {0}".format(sys.version.split()[0]))

required_modules = [
    "argparse", "base64", "hashlib", "html", "http.server", "json", "os", "pathlib",
    "shlex", "sqlite3", "subprocess", "threading", "urllib.request", "ssl",
]
for module in required_modules:
    try:
        importlib.import_module(module)
        ok("python stdlib module available: {0}".format(module))
    except Exception as exc:
        fail("python stdlib module unavailable: {0}: {1}".format(module, exc))
ok("external pip packages: not required")

sys.path.insert(0, str(Path(app_home) / "app"))
try:
    from oas_admin_lite.config import load_config
except Exception as exc:
    fail("cannot import oas_admin_lite config module: {0}".format(exc))
    sys.exit(1)

try:
    cfg = load_config(config_file)
except Exception as exc:
    fail("cannot load config file {0}: {1}".format(config_file, exc))
    sys.exit(1)

check_path("APP_HOME", app_home, "dir", writable=True)
check_path("Config file", config_file, "file")
for label, value in (
    ("Data directory", cfg.paths.data_dir),
    ("Log directory", cfg.paths.log_dir),
    ("Backup directory", cfg.paths.backup_dir),
    ("Bundle directory", cfg.paths.bundle_dir),
    ("Package directory", cfg.paths.package_dir),
):
    check_path(label, value, "dir", writable=True)

try:
    host, port = split_listen(cfg.server.listen)
    check_bind_available("OAS Admin Lite", host, port)
except Exception as exc:
    warn("cannot validate OAS Admin Lite listen address {0}: {1}".format(cfg.server.listen, exc))

check_path("OAS ORACLE_HOME", cfg.oas.oracle_home, "dir", executable=True)
check_path("OAS DOMAIN_HOME", cfg.oas.domain_home, "dir", executable=True)
check_path("OAS bitools/bin", cfg.oas.bitools_bin, "dir", executable=True)
check_path("OPatch executable", os.path.join(cfg.oas.oracle_home, "OPatch", "opatch"), "file", executable=True)
for script in cfg.scripts.allowed or []:
    check_path("OAS script {0}".format(script), os.path.join(cfg.oas.bitools_bin, script), "file", executable=True)

ohs = getattr(cfg, "ohs", None)
if ohs and getattr(ohs, "monitor_local", False):
    check_path("OHS ORACLE_HOME", getattr(ohs, "oracle_home", ""), "dir", required=False, executable=True)
    check_path("OHS DOMAIN_HOME", getattr(ohs, "domain_home", ""), "dir", required=False, executable=True)
    http_port = str(getattr(ohs, "http_port", "") or "").strip()
    https_port = str(getattr(ohs, "https_port", "") or "").strip()
    if http_port:
        check_tcp_connect("OHS HTTP", "127.0.0.1", http_port)
    else:
        warn("OHS http_port is not configured")
    if https_port:
        check_tcp_connect("OHS HTTPS", "127.0.0.1", https_port)
elif ohs:
    ok("OHS local checks skipped (ohs.monitor_local=false; OHS may be on a separate Web-tier host)")

endpoint = getattr(cfg.oas, "catalog_api_url", "") or "{0}{1}".format(getattr(cfg.oas, "catalog_base_url", ""), getattr(cfg.oas, "catalog_api_path", ""))
parsed = urlparse(endpoint)
if parsed.scheme in ("http", "https") and parsed.netloc:
    ok("Catalog REST endpoint is configured: {0}".format(endpoint))
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        check_tcp_connect("Catalog REST network path", parsed.hostname, port)
    except Exception as exc:
        warn("cannot validate Catalog REST network path: {0}".format(exc))
else:
    warn("Catalog REST endpoint is not a complete http/https URL: {0}".format(endpoint))

if failures:
    print("healthcheck result: FAILED ({0} failure(s), {1} warning(s))".format(failures, warnings))
    sys.exit(1)
print("healthcheck result: OK ({0} warning(s))".format(warnings))
PY
  PY_STATUS=$?
  if [ "$PY_STATUS" -ne 0 ]; then
    fail "environment/OAS/OHS prerequisite check failed"
  else
    ok "environment/OAS/OHS prerequisite check completed"
  fi
else
  fail "skipped environment checks because python or entrypoint is missing"
fi

section "Summary"
if [ "$FAILURES" -gt 0 ]; then
  printf '[FAIL] healthcheck failed: %s failure(s), %s warning(s)\n' "$FAILURES" "$WARNINGS"
  exit 1
fi
printf '[OK] healthcheck completed: %s warning(s)\n' "$WARNINGS"
