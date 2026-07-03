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
                self._redirect("/resources")
            elif path == "/static/app.css":
                self._static_css()
            elif path == "/dashboard":
                self._redirect("/resources")
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
                fallback = "/resources"
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


SCRIPT_GUIDES = [
    {
        "name": "datamodel.sh",
        "purpose": "BAR 파일 또는 서비스 인스턴스에서 semantic model, connection, data model 관련 정보를 추출하거나 관리할 때 사용합니다.",
        "usage": "예: -help 로 사용 가능한 옵션을 먼저 확인한 뒤, 필요한 BAR 파일 또는 service instance 인자를 지정합니다.",
        "result": "결과 파일과 stdout/stderr를 Jobs / Audit에서 확인하고, 모델 이관 전후 비교나 지원 요청 자료로 활용합니다.",
    },
    {
        "name": "diagnostic_dump.sh",
        "purpose": "OAS service instance 진단 정보를 묶어 장애 분석용 dump를 생성합니다.",
        "usage": "장애 직후 실행해 로그와 진단 자료를 보존합니다. 실행 전 저장 경로의 여유 공간을 확인하세요.",
        "result": "생성된 dump는 SR, 내부 분석, 패치 전후 상태 비교에 사용합니다. 파일 경로는 실행 결과와 Jobs / Audit에 남깁니다.",
    },
    {
        "name": "exportarchive.sh",
        "purpose": "Catalog, security, semantic model 등 OAS 산출물을 BAR archive로 내보낼 때 사용합니다.",
        "usage": "예: -serviceInstance ssi -bar /u01/oas-admin-lite/backups/catalog/export.bar 처럼 대상 instance와 BAR 경로를 지정합니다.",
        "result": "생성된 BAR 파일은 백업, 이관, import 전 안전 지점으로 사용합니다. 파일은 /u01/oas-admin-lite/backups 아래 보관하는 것을 권장합니다.",
    },
    {
        "name": "importarchive.sh",
        "purpose": "BAR archive를 OAS service instance로 가져와 catalog 또는 관련 메타데이터를 반영할 때 사용합니다.",
        "usage": "실행 전 exportarchive.sh로 현재 상태를 백업하고, 대상 BAR 파일과 옵션을 Preview에서 확인한 뒤 RUN을 입력합니다.",
        "result": "성공 후 OAS 화면에서 주요 대시보드/분석을 확인하고, 실패 시 Jobs / Audit의 stdout/stderr와 BAR 파일 경로를 같이 검토합니다.",
    },
]

PAGE_DESCRIPTIONS = {
    "resources": "OAS 서버의 CPU, Memory, Swap, /u01 Disk, Listener, Process 상태와 주요 런타임 경로를 확인합니다. 앱 설정값은 Settings에서 확인합니다.",
    "catalog": "OAS REST API를 호출해 카탈로그 object 현황을 수집합니다. Endpoint, 인증 사용자, HTTP 상태와 응답 형식을 함께 확인합니다.",
    "patch": "현재 ORACLE_HOME의 OPatch inventory를 조회해 설치된 패치 레벨을 확인합니다. 이 화면은 조회 전용이며 패치를 적용하지 않습니다.",
    "scripts": "허용된 OAS 관리 스크립트만 Preview 후 실행합니다. import/export 및 diagnostic 작업 결과는 Jobs / Audit에 기록됩니다.",
    "jobs": "Catalog 수집, OPatch, OAS 스크립트 실행 이력을 조회합니다. 명령, 결과, 메시지를 audit trail로 확인합니다.",
    "settings": "현재 앱 설정과 OAS 경로, Catalog REST 설정을 표시합니다. 1차 버전에서는 설정 파일을 직접 수정하는 방식입니다.",
}


def layout(ctx, title, active, content, query):
    flash = html.escape(first(query, "flash"))
    error = html.escape(first(query, "error"))
    description = PAGE_DESCRIPTIONS.get(active, "")
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
        <div><h1>{title}</h1><p class="page-description">{description}</p><p class="page-path">{root}</p></div>
        <div class="status-pill">{auth}</div>
      </header>
      {notice}
      {content}
    </main>
  </div>
</body>
</html>""".format(title=esc(title), nav=nav(active), description=esc(description), root=esc(ctx.cfg.paths.root), auth="Auth Enabled" if auth_enabled else "Local Mode", notice=notice, content=content)


def nav(active):
    items = [
        ("resources", "Resources", "/resources"),
        ("catalog", "Catalog", "/catalog"),
        ("patch", "Patch", "/patch"),
        ("scripts", "Scripts", "/scripts"),
        ("jobs", "Jobs / Audit", "/jobs"),
        ("settings", "Settings", "/settings"),
    ]
    return "".join('<a class="{0}" href="{1}">{2}</a>'.format("active" if key == active else "", href, label) for key, label, href in items)

def resources_page(ctx, query):
    snap = ctx.resources.snapshot()
    oas_cards = "".join(path_card(check) for check in snap.oas_checks)
    metric_cards = "".join(metric_card(metric) for metric in snap.metrics)
    resource_rows = "".join(check_row(c, value_second=False) for c in snap.resource_checks)
    content = """
<section class="panel">
  <div class="panel-head"><h2>서버 식별 정보</h2><a class="button secondary" href="/resources">새로고침</a></div>
  {snapshot}
</section>
<section class="panel">
  <div class="panel-head"><h2>서버 리소스 요약</h2></div>
  {legend}
  <div class="metric-grid">{metric_cards}</div>
</section>
<section class="panel">
  <div class="panel-head"><h2>OAS 런타임 경로</h2></div>
  <div class="status-grid">{oas_cards}</div>
</section>
<section class="panel">
  <div class="panel-head"><h2>리스너 및 프로세스 상세</h2></div>
  <table><thead><tr><th>항목</th><th>상태</th><th>값</th><th>상세</th></tr></thead><tbody>{resource_rows}</tbody></table>
</section>
""".format(snapshot=snapshot_kv(snap), legend=metric_status_legend(), metric_cards=metric_cards, oas_cards=oas_cards, resource_rows=resource_rows)
    return layout(ctx, "Resources", "resources", content, query)

def path_card(check):
    status_class = "" if check.status == "OK" else check.status
    note = "" if check.status == "OK" else badge(check.status)
    return """
    <div class="status-card path-card {status_class}">
      <div class="status-card-head"><span>{name}</span>{note}</div>
      <pre>{value}</pre>
      <p>{detail}</p>
    </div>
    """.format(status_class=esc(status_class), name=esc(check.name), note=note, value=esc(check.value), detail=esc(check.detail))


def metric_status_legend():
    return """
    <div class="status-legend">
      <span><strong class="status-word OK">OK</strong> 정상 범위입니다.</span>
      <span><strong class="status-word WARN">WARN</strong> 운영자가 확인해야 할 임계치에 근접했거나 일부 조회가 제한된 상태입니다.</span>
      <span><strong class="status-word FAILED">FAILED</strong> 실패 임계치를 넘었거나 리소스 위험도가 높은 상태입니다.</span>
    </div>
    """


def metric_card(metric):
    percent = int(metric.percent or 0)
    if percent < 0:
        percent = 0
    if percent > 100:
        percent = 100
    return """
    <div class="metric-card {status}">
      <div class="metric-head"><span>{name}</span>{badge}</div>
      <div class="metric-value"><strong>{value}</strong><span>{unit}</span></div>
      <div class="meter"><span style="width:{percent}%"></span></div>
      <div class="metric-foot"><span>{percent}%</span><span>{detail}</span></div>
    </div>
    """.format(status=esc(metric.status), name=esc(metric.name), badge=badge(metric.status), value=esc(metric.value), unit=esc(metric.unit), percent=percent, detail=esc(metric.detail))

def catalog_page(ctx, query):
    summary = ctx.catalog.last_summary()
    counts = summary.get("counts") or {}
    if counts:
        rows = "".join("<tr><td>{0}</td><td>{1}</td></tr>".format(esc(k), v) for k, v in sorted(counts.items()))
    elif summary.get("last_scan", 0):
        rows = '<tr><td colspan="2">집계 가능한 object type이 없습니다. 위의 Message와 Content-Type을 확인하세요.</td></tr>'
    else:
        rows = '<tr><td colspan="2">아직 카탈로그 수집을 실행하지 않았습니다.</td></tr>'
    content = """
<section class="panel">
  <div class="panel-head"><h2>카탈로그 현황</h2><form method="post" action="/catalog/scan"><button type="submit">수집 실행</button></form></div>
  <dl class="kv compact"><dt>Endpoint</dt><dd>{endpoint}</dd><dt>Auth User</dt><dd>{auth_user}</dd><dt>Last Scan</dt><dd>{last_scan}</dd><dt>Status</dt><dd>{status}</dd><dt>HTTP</dt><dd>{http_status}</dd><dt>Content-Type</dt><dd>{content_type}</dd><dt>Message</dt><dd>{message}</dd></dl>
  <table><thead><tr><th>유형</th><th>개수</th></tr></thead><tbody>{rows}</tbody></table>
</section>
""".format(endpoint=esc(summary.get("endpoint", "")), last_scan=fmt_ts(summary.get("last_scan", 0)), status=esc(summary.get("status", "")), http_status=esc(summary.get("http_status", "")), content_type=esc(summary.get("content_type", "")), message=esc(summary.get("message", "")), auth_user=esc(summary.get("auth_user", "")), rows=rows)
    return layout(ctx, "Catalog", "catalog", content, query)


def patch_page(ctx, query):
    state = ctx.patch.state_dict()
    content = """
<section class="panel">
  <div class="panel-head"><h2>현재 패치 레벨</h2><form method="post" action="/patch/inventory"><button type="submit">현재 패치 조회</button></form></div>
  <dl class="kv compact"><dt>ORACLE_HOME</dt><dd>{oracle_home}</dd><dt>OPatch</dt><dd>{opatch}</dd></dl>
  {result}
</section>
""".format(oracle_home=esc(state.get("oracle_home", "")), opatch=esc(state.get("opatch_path", "")), result=result_block(state.get("last_command", ""), state.get("last_output", "")))
    return layout(ctx, "Patch", "patch", content, query)


def scripts_page(ctx, query):
    state = ctx.scripts.state_dict()
    options = "".join('<option value="{0}">{0}</option>'.format(esc(item)) for item in state.get("allowed", []))
    allowed = "".join("<span class=\"tag\">{0}</span>".format(esc(item)) for item in state.get("allowed", []))
    guide_cards = "".join(script_guide_card(item) for item in SCRIPT_GUIDES)
    content = """
<section class="panel">
  <div class="panel-head"><h2>실행 정책</h2></div>
  <dl class="kv compact"><dt>bitools/bin</dt><dd>{bitools}</dd><dt>허용 스크립트</dt><dd class="tag-list">{allowed}</dd></dl>
  <div class="info-list">
    <div><strong>이 화면에서 가능한 일</strong><p>허용된 OAS 스크립트의 실행 명령을 Preview로 확인하고, RUN 확인 입력 후 oracle 계정 권한으로 실행합니다.</p></div>
    <div><strong>안전 정책</strong><p>임의 shell 명령은 실행하지 않습니다. 스크립트 이름은 allowlist에서만 선택하며, 실행 결과는 Jobs / Audit에 남습니다.</p></div>
    <div><strong>결과 활용</strong><p>stdout/stderr와 생성 파일 경로를 작업 이력에서 확인해 백업, 이관, 장애 분석, SR 자료로 활용합니다.</p></div>
  </div>
</section>
<section class="panel">
  <div class="panel-head"><h2>스크립트별 기능 및 사용 방법</h2></div>
  <div class="script-grid">{guide_cards}</div>
</section>
<section class="panel">
  <div class="panel-head"><h2>스크립트 실행</h2></div>
  <form method="post" class="stack">
    <label>Script<select name="script">{options}</select></label>
    <label>Arguments<input name="args" placeholder="-serviceInstance ssi -bar /u01/oas-admin-lite/backups/export.bar"></label>
    <div class="actions"><button formaction="/scripts/preview" type="submit" class="secondary">Preview</button></div>
    <label>실행 확인<input name="confirm" placeholder="RUN"></label>
    <button formaction="/scripts/run" type="submit" class="danger">실행</button>
  </form>
  {result}
</section>
""".format(bitools=esc(state.get("bitools_bin", "")), allowed=allowed, guide_cards=guide_cards, options=options, result=result_block(state.get("last_command", ""), state.get("last_output", "")))
    return layout(ctx, "Scripts", "scripts", content, query)


def script_guide_card(item):
    return """
    <article class="script-card">
      <h3>{name}</h3>
      <dl>
        <dt>기능</dt><dd>{purpose}</dd>
        <dt>사용 방법</dt><dd>{usage}</dd>
        <dt>결과 활용</dt><dd>{result}</dd>
      </dl>
    </article>
    """.format(name=esc(item["name"]), purpose=esc(item["purpose"]), usage=esc(item["usage"]), result=esc(item["result"]))

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
        ("Catalog Base URL", getattr(cfg.oas, "catalog_base_url", "")),
        ("Catalog API Path", getattr(cfg.oas, "catalog_api_path", "")),
        ("Catalog API URL", getattr(cfg.oas, "catalog_api_url", "")),
        ("Catalog Username", getattr(cfg.oas, "catalog_username", "")),
        ("Catalog Password", "configured" if getattr(cfg.oas, "catalog_password", "") else ""),
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