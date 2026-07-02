import base64
import hashlib
import html
import os
import socketserver
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .catalog import CatalogService
from .patching import PatchService
from .resources import ResourceCollector
from .scripts_runner import ScriptService
from .storage import JobStore


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


class AppContext(object):
    def __init__(self, cfg):
        cfg.ensure_dirs()
        self.cfg = cfg
        self.store = JobStore(os.path.join(cfg.paths.data_dir, "oas-admin-lite.db"))
        self.resources = ResourceCollector(cfg)
        self.catalog = CatalogService(cfg, self.store)
        self.patch = PatchService(cfg, self.store)
        self.scripts = ScriptService(cfg, self.store)


def make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OASAdminLite/0.1"

        def do_GET(self):
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                self._redirect("/dashboard")
            elif path == "/static/app.css":
                self._static_css()
            elif path == "/dashboard":
                self._html(dashboard_page(ctx, query))
            elif path == "/resources":
                self._html(resources_page(ctx, query))
            elif path == "/catalog":
                self._html(catalog_page(ctx, query))
            elif path == "/patch":
                self._html(patch_page(ctx, query))
            elif path == "/scripts":
                self._html(scripts_page(ctx, query))
            elif path == "/jobs":
                self._html(jobs_page(ctx, query))
            elif path == "/settings":
                self._html(settings_page(ctx, query))
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self):
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            form = self._form()
            try:
                if parsed.path == "/catalog/scan":
                    ctx.catalog.scan()
                    self._redirect_flash("/catalog", "카탈로그 수집 작업이 완료되었습니다.")
                elif parsed.path == "/patch/inventory":
                    ctx.patch.inventory()
                    self._redirect_flash("/patch", "현재 패치 레벨 조회가 완료되었습니다.")
                elif parsed.path == "/patch/precheck":
                    ctx.patch.precheck(first(form, "patch_path"))
                    self._redirect_flash("/patch", "패치 사전 점검이 완료되었습니다.")
                elif parsed.path == "/patch/preview":
                    ctx.patch.preview(first(form, "patch_path"))
                    self._redirect_flash("/patch", "패치 실행 명령 Preview가 생성되었습니다.")
                elif parsed.path == "/patch/apply":
                    if first(form, "confirm") != "APPLY":
                        raise ValueError("실행하려면 확인 입력란에 APPLY를 입력해야 합니다.")
                    ctx.patch.apply(first(form, "patch_path"))
                    self._redirect_flash("/patch", "패치 실행이 완료되었습니다. 상세 로그를 확인하세요.")
                elif parsed.path == "/scripts/preview":
                    ctx.scripts.preview(first(form, "script"), first(form, "args"))
                    self._redirect_flash("/scripts", "스크립트 실행 명령 Preview가 생성되었습니다.")
                elif parsed.path == "/scripts/run":
                    if first(form, "confirm") != "RUN":
                        raise ValueError("실행하려면 확인 입력란에 RUN을 입력해야 합니다.")
                    ctx.scripts.run(first(form, "script"), first(form, "args"))
                    self._redirect_flash("/scripts", "스크립트 실행이 완료되었습니다.")
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "not found")
            except Exception as exc:
                fallback = "/dashboard"
                if parsed.path.startswith("/catalog"):
                    fallback = "/catalog"
                elif parsed.path.startswith("/patch"):
                    fallback = "/patch"
                elif parsed.path.startswith("/scripts"):
                    fallback = "/scripts"
                self._redirect_error(fallback, str(exc))

        def _form(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            return parse_qs(body)

        def _html(self, body):
            payload = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _static_css(self):
            path = os.path.join(os.path.dirname(__file__), "static", "app.css")
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, path):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", path)
            self.end_headers()

        def _redirect_flash(self, path, message):
            self._redirect("{0}?flash={1}".format(path, quote(message)))

        def _redirect_error(self, path, message):
            self._redirect("{0}?error={1}".format(path, quote(message)))

        def _authorized(self):
            expected_hash = (os.environ.get("OAS_ADMIN_LITE_PASSWORD_SHA256") or ctx.cfg.security.password_sha256 or "").strip().lower()
            if expected_hash.startswith("sha256:"):
                expected_hash = expected_hash.split(":", 1)[1]
            if not expected_hash:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                self._auth_required()
                return False
            try:
                decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except Exception:
                self._auth_required()
                return False
            actual = hashlib.sha256(password.encode("utf-8")).hexdigest()
            if username != ctx.cfg.security.username or actual != expected_hash:
                self._auth_required()
                return False
            return True

        def _auth_required(self):
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="OAS Admin Lite"')
            self.end_headers()

        def log_message(self, fmt, *args):
            print("{0} - {1}".format(self.address_string(), fmt % args))

    return Handler


def first(form, key):
    values = form.get(key) or [""]
    return values[0]


def layout(ctx, title, active, content, query):
    flash = html.escape(first(query, "flash"))
    error = html.escape(first(query, "error"))
    auth_enabled = bool(os.environ.get("OAS_ADMIN_LITE_PASSWORD_SHA256") or ctx.cfg.security.password_sha256)
    notice = ""
    if flash:
        notice += '<div class="notice success">{0}</div>'.format(flash)
    if error:
        notice += '<div class="notice error">{0}</div>'.format(error)
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - OAS Admin Lite</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">OAS Admin Lite</div>
      <nav>{nav}</nav>
    </aside>
    <main class="content">
      <header class="topbar">
        <div><h1>{title}</h1><p>{root}</p></div>
        <div class="status-pill">{auth}</div>
      </header>
      {notice}
      {content}
    </main>
  </div>
</body>
</html>""".format(title=esc(title), nav=nav(active), root=esc(ctx.cfg.paths.root), auth="Auth Enabled" if auth_enabled else "Local Mode", notice=notice, content=content)


def nav(active):
    items = [
        ("dashboard", "Dashboard", "/dashboard"),
        ("resources", "Resources", "/resources"),
        ("catalog", "Catalog", "/catalog"),
        ("patch", "Patch", "/patch"),
        ("scripts", "Scripts", "/scripts"),
        ("jobs", "Jobs / Audit", "/jobs"),
        ("settings", "Settings", "/settings"),
    ]
    return "".join('<a class="{0}" href="{1}">{2}</a>'.format("active" if key == active else "", href, label) for key, label, href in items)


def dashboard_page(ctx, query):
    snap = ctx.resources.snapshot()
    recent = ctx.store.list(5)
    checks = "".join(check_row(c) for c in snap.checks)
    jobs = "".join(job_row(j, compact=True) for j in recent) or '<tr><td colspan="3">작업 이력이 없습니다.</td></tr>'
    content = """
<section class="grid two">
  <div class="panel"><h2>서버 요약</h2>{snapshot}</div>
  <div class="panel"><h2>최근 작업</h2><table><thead><tr><th>시간</th><th>작업</th><th>결과</th></tr></thead><tbody>{jobs}</tbody></table></div>
</section>
<section class="panel"><h2>주요 점검</h2><table><thead><tr><th>항목</th><th>값</th><th>상태</th><th>상세</th></tr></thead><tbody>{checks}</tbody></table></section>
""".format(snapshot=snapshot_kv(snap), jobs=jobs, checks=checks)
    return layout(ctx, "Dashboard", "dashboard", content, query)


def resources_page(ctx, query):
    snap = ctx.resources.snapshot()
    rows = "".join(check_row(c, value_second=False) for c in snap.checks)
    content = """
<section class="panel">
  <div class="panel-head"><h2>서버 리소스</h2><a class="button secondary" href="/resources">새로고침</a></div>
  {snapshot}
  <table><thead><tr><th>항목</th><th>상태</th><th>값</th><th>상세</th></tr></thead><tbody>{rows}</tbody></table>
</section>
""".format(snapshot=snapshot_kv(snap), rows=rows)
    return layout(ctx, "Resources", "resources", content, query)


def catalog_page(ctx, query):
    summary = ctx.catalog.last_summary()
    counts = summary.get("counts") or {}
    rows = "".join("<tr><td>{0}</td><td>{1}</td></tr>".format(esc(k), v) for k, v in sorted(counts.items())) or '<tr><td colspan="2">수집된 카탈로그 집계가 없습니다.</td></tr>'
    content = """
<section class="panel">
  <div class="panel-head"><h2>카탈로그 현황</h2><form method="post" action="/catalog/scan"><button type="submit">수집 실행</button></form></div>
  <dl class="kv compact"><dt>Endpoint</dt><dd>{endpoint}</dd><dt>Last Scan</dt><dd>{last_scan}</dd><dt>Message</dt><dd>{message}</dd></dl>
  <table><thead><tr><th>유형</th><th>개수</th></tr></thead><tbody>{rows}</tbody></table>
</section>
""".format(endpoint=esc(summary.get("endpoint", "")), last_scan=fmt_ts(summary.get("last_scan", 0)), message=esc(summary.get("message", "")), rows=rows)
    return layout(ctx, "Catalog", "catalog", content, query)


def patch_page(ctx, query):
    state = ctx.patch.state_dict()
    allowed = "".join("<div>{0}</div>".format(esc(item)) for item in state.get("allowed_patch_dirs", []))
    content = """
<section class="panel">
  <h2>패치 및 업데이트</h2>
  <dl class="kv compact"><dt>ORACLE_HOME</dt><dd>{oracle_home}</dd><dt>OPatch</dt><dd>{opatch}</dd><dt>허용 경로</dt><dd>{allowed}</dd></dl>
  <div class="actions"><form method="post" action="/patch/inventory"><button type="submit" class="secondary">현재 패치 조회</button></form></div>
  <form method="post" class="stack">
    <label>Patch Directory<input name="patch_path" placeholder="/u01/oas-admin-lite/packages/patches/patch_id"></label>
    <div class="actions"><button formaction="/patch/precheck" type="submit" class="secondary">사전 점검</button><button formaction="/patch/preview" type="submit" class="secondary">Preview</button></div>
    <label>실행 확인<input name="confirm" placeholder="APPLY"></label>
    <button formaction="/patch/apply" type="submit">패치 실행</button>
  </form>
  {result}
</section>
""".format(oracle_home=esc(state.get("oracle_home", "")), opatch=esc(state.get("opatch_path", "")), allowed=allowed, result=result_block(state.get("last_command", ""), state.get("last_output", "")))
    return layout(ctx, "Patch", "patch", content, query)


def scripts_page(ctx, query):
    state = ctx.scripts.state_dict()
    options = "".join('<option value="{0}">{0}</option>'.format(esc(item)) for item in state.get("allowed", []))
    allowed = "".join("<div>{0}</div>".format(esc(item)) for item in state.get("allowed", []))
    content = """
<section class="panel">
  <h2>OAS 관리 스크립트</h2>
  <dl class="kv compact"><dt>bitools/bin</dt><dd>{bitools}</dd><dt>허용 스크립트</dt><dd>{allowed}</dd></dl>
  <form method="post" class="stack">
    <label>Script<select name="script">{options}</select></label>
    <label>Arguments<input name="args" placeholder="-serviceInstance ssi -bar /u01/oas-admin-lite/backups/export.bar"></label>
    <div class="actions"><button formaction="/scripts/preview" type="submit" class="secondary">Preview</button></div>
    <label>실행 확인<input name="confirm" placeholder="RUN"></label>
    <button formaction="/scripts/run" type="submit">실행</button>
  </form>
  {result}
</section>
""".format(bitools=esc(state.get("bitools_bin", "")), allowed=allowed, options=options, result=result_block(state.get("last_command", ""), state.get("last_output", "")))
    return layout(ctx, "Scripts", "scripts", content, query)


def jobs_page(ctx, query):
    rows = "".join(job_row(job) for job in ctx.store.list(100)) or '<tr><td colspan="5">작업 이력이 없습니다.</td></tr>'
    content = '<section class="panel"><h2>작업 이력</h2><table><thead><tr><th>시간</th><th>작업</th><th>명령</th><th>결과</th><th>메시지</th></tr></thead><tbody>{0}</tbody></table></section>'.format(rows)
    return layout(ctx, "Jobs / Audit", "jobs", content, query)


def settings_page(ctx, query):
    cfg = ctx.cfg
    values = [
        ("Listen", cfg.server.listen),
        ("Root", cfg.paths.root),
        ("Data", cfg.paths.data_dir),
        ("Logs", cfg.paths.log_dir),
        ("Backups", cfg.paths.backup_dir),
        ("Bundles", cfg.paths.bundle_dir),
        ("Packages", cfg.paths.package_dir),
        ("ORACLE_HOME", cfg.oas.oracle_home),
        ("DOMAIN_HOME", cfg.oas.domain_home),
        ("bitools/bin", cfg.oas.bitools_bin),
        ("Analytics URL", cfg.oas.analytics_url),
    ]
    rows = "".join("<dt>{0}</dt><dd>{1}</dd>".format(esc(k), esc(v)) for k, v in values)
    content = '<section class="panel"><h2>설정</h2><dl class="kv">{0}</dl><p class="muted">1차 버전에서는 화면에서 설정을 수정하지 않고 app.yaml을 읽어 표시합니다.</p></section>'.format(rows)
    return layout(ctx, "Settings", "settings", content, query)


def snapshot_kv(snap):
    return '<dl class="kv compact"><dt>Hostname</dt><dd>{0}</dd><dt>OS</dt><dd>{1} / {2}</dd><dt>Checked</dt><dd>{3}</dd></dl>'.format(esc(snap.hostname), esc(snap.os_name), esc(snap.arch), fmt_ts(snap.checked_at))


def check_row(check, value_second=True):
    if value_second:
        cells = "<td>{0}</td><td><pre>{1}</pre></td><td>{2}</td><td>{3}</td>".format(esc(check.name), esc(check.value), badge(check.status), esc(check.detail))
    else:
        cells = "<td>{0}</td><td>{1}</td><td><pre>{2}</pre></td><td>{3}</td>".format(esc(check.name), badge(check.status), esc(check.value), esc(check.detail))
    return "<tr>{0}</tr>".format(cells)


def job_row(job, compact=False):
    if compact:
        return "<tr><td>{0}</td><td>{1}</td><td>{2}</td></tr>".format(fmt_ts(job["started_at"], "%H:%M:%S"), esc(job["type"]), badge(job["status"]))
    return "<tr><td>{0}</td><td>{1}</td><td><pre>{2}</pre></td><td>{3}</td><td><pre>{4}</pre></td></tr>".format(fmt_ts(job["started_at"]), esc(job["type"]), esc(job["command"]), badge(job["status"]), esc(job["message"]))


def result_block(command, output):
    if not command:
        return ""
    return '<div class="result"><h3>최근 명령</h3><pre>{0}</pre><h3>최근 결과</h3><pre>{1}</pre></div>'.format(esc(command), esc(output))


def badge(status):
    return '<span class="badge {0}">{0}</span>'.format(esc(status))


def fmt_ts(value, fmt="%Y-%m-%d %H:%M:%S"):
    try:
        value = float(value)
    except Exception:
        value = 0.0
    if value <= 0:
        return "-"
    return time.strftime(fmt, time.localtime(value))


def esc(value):
    return html.escape(str(value), quote=True)


def serve(ctx):
    host, port = split_listen(ctx.cfg.server.listen)
    httpd = ThreadingHTTPServer((host, port), make_handler(ctx))
    print("oas-admin-lite listening on http://{0}".format(ctx.cfg.server.listen))
    httpd.serve_forever()


def split_listen(listen):
    if ":" not in listen:
        return listen, 18080
    host, port = listen.rsplit(":", 1)
    return host, int(port)