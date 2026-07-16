import base64
import hashlib
import html
import os
import shlex
import socketserver
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .catalog import CatalogService
from .patching import PatchService
from .resources import ResourceCollector
from .scripts_runner import ScriptService, allowed_scripts
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
            elif path == "/static/app.js":
                self._static_js()
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
                    script, args, stdin_text, stdin_label = script_request(form)
                    form_state = script_form_state(form)
                    ctx.scripts.preview(script, args, stdin_text, stdin_label)
                    ctx.store.set_json("script_form_state", form_state)
                    ctx.store.set_json("script_preview_state", form_state)
                    self._redirect_flash(script_redirect(script), "명령어 미리보기를 생성했습니다.")
                elif parsed.path == "/scripts/run":
                    script, args, stdin_text, stdin_label = script_request(form)
                    form_state = script_form_state(form)
                    if not script_preview_matches(ctx.store.get_json("script_preview_state", {}), form_state):
                        raise ValueError("현재 입력값으로 명령어 미리보기를 먼저 수행하세요.")
                    ctx.store.set_json("script_form_state", form_state)
                    ctx.scripts.run(script, args, stdin_text, stdin_label)
                    self._redirect_flash(script_redirect(script), "실제 OAS 스크립트 실행이 완료되었습니다.")
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "not found")
            except Exception as exc:
                fallback = "/resources"
                if parsed.path.startswith("/catalog"):
                    fallback = "/catalog"
                elif parsed.path.startswith("/patch"):
                    fallback = "/patch"
                elif parsed.path.startswith("/scripts"):
                    fallback = script_redirect(first(form, "script"))
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
            self._static_file("app.css", "text/css; charset=utf-8")

        def _static_js(self):
            self._static_file("app.js", "application/javascript; charset=utf-8")

        def _static_file(self, filename, content_type):
            path = os.path.join(os.path.dirname(__file__), "static", filename)
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def _redirect(self, path):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", path)
            self.end_headers()

        def _redirect_flash(self, path, message):
            sep = "&" if "?" in path else "?"
            self._redirect("{0}{1}flash={2}".format(path, sep, quote(message)))

        def _redirect_error(self, path, message):
            sep = "&" if "?" in path else "?"
            self._redirect("{0}{1}error={2}".format(path, sep, quote(message)))

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


SCRIPT_ACTIONS = [
    {
        "script": "exportarchive.sh",
        "mode": "exportarchive",
        "short_label": "\ud658\uacbd \ubc31\uc5c5",
        "label": "OAS 환경 백업 내보내기",
        "method": "exportarchive.sh <service instance key> <export directory> {optional parameters} < /path/exportpwd.txt",
        "purpose": "서비스 인스턴스의 환경 메타데이터를 BAR 파일로 내보내 이관, 백업, 비교 작업에 사용할 수 있게 합니다.",
        "required": ["Service instance key", "Export directory", "Encryption password file path: password 한 줄만 저장된 서버 파일 경로"],
        "optional": ["noconnectionparams", "nouserfolders", "includedata", "advancedoptions=<path> 등 exportarchive.sh help 기준 옵션", "encryptionpassword는 입력하지 않음(password file을 stdin으로 전달)"],
        "result": "/u01/oas-admin-lite/backups 하위 export directory에 BAR 내보내기 결과가 생성됩니다. 실행 로그와 exit code는 Jobs / Audit에서 확인합니다.",
        "cautions": [
            "웹앱은 password 파일 내용을 읽지 않습니다. 사전에 준비한 password 파일 경로만 명령어에 포함합니다.",
            "실행 시 exportarchive.sh 명령이 < 파일경로 형태로 실행됩니다. 파일에는 password 한 줄만 저장하고 chmod 600 권한을 권장합니다.",
            "실제 실행 버튼을 누르면 OAS 서버의 bitools/bin에서 exportarchive.sh가 실행됩니다.",
        ],
    },
    {
        "script": "diagnostic_dump.sh",
        "mode": "diagnostic",
        "short_label": "\uc9c4\ub2e8 \ub85c\uadf8",
        "label": "OAS 지원용 진단 로그 수집",
        "method": "diagnostic_dump.sh <zip file name>",
        "purpose": "Oracle Support 또는 Development 조직에 제공할 OAS 진단 번들 ZIP을 수집합니다.",
        "required": ["ZIP file name"],
        "optional": ["없음"],
        "result": "/u01/oas-admin-lite/bundles 하위에 진단 ZIP이 생성됩니다. stdout/stderr, exit code, 진단 로그 경로는 Jobs / Audit에서 확인합니다.",
        "cautions": [
            "진단 번들은 환경 및 보안 설정 정보를 포함할 수 있으므로 공유 대상을 제한합니다.",
            "실행 시간이 걸릴 수 있고 결과 ZIP 파일 크기가 커질 수 있습니다.",
            "실제 실행 버튼을 누르면 OAS 서버의 bitools/bin에서 diagnostic_dump.sh가 실행됩니다.",
        ],
    },
    {
        "script": "runcat.sh", "mode": "catalog_report", "label": "\uce74\ud0c8\ub85c\uadf8 \uac10\uc0ac \ubcf4\uace0\uc11c \ucd94\ucd9c",
        "short_label": "\uac10\uc0ac \ubcf4\uace0\uc11c",
        "method": "runcat.sh -cmd report -outputFile <CSV file> -type <type> -fields \"field:field\" [-folder <path>] [-recursionDepth <n>] [-skipTypes <type>] [-excelFormat] -online <OBIPS URL> -credentials <properties file>",
        "purpose": "Oracle Analytics \uce74\ud0c8\ub85c\uadf8\uc758 \uac1d\uccb4, \uc18c\uc720\uc790, ACL \uc815\ubcf4\ub97c \uc77d\uae30 \uc804\uc6a9 CSV \uac10\uc0ac \ubcf4\uace0\uc11c\ub85c \ucd94\ucd9c\ud569\ub2c8\ub2e4.",
        "required": ["Report output file", "Object type", "Report fields", "OBIPS URL", "Credentials properties file"],
        "optional": ["Folder, recursion depth, skipped types/folders, delimiter, duplicate removal", "Excel format and force output only when needed"],
        "result": "The CSV report is generated at the selected path. Logs and exit code are available in Jobs / Audit.",
        "cautions": ["Only read-only runcat.sh -cmd report is generated.", "Credentials file contents are never read or displayed by this application.", "Force output may replace an existing report file."],
    },
]

PAGE_DESCRIPTIONS = {
    "resources": "서버 리소스, OAS 기동 상태와 관리 화면 접속 경로를 한눈에 확인합니다.",
    "catalog": "카탈로그 자산 유형, 폴더별 자산 수와 소유자별 상세 목록을 확인합니다.",
    "patch": "ORACLE_HOME의 OPatch inventory를 조회하여 적용된 패치 수준을 확인합니다.",
    "scripts": "OAS 백업·진단 스크립트의 명령어를 미리 확인한 뒤 안전하게 실행합니다.",
    "jobs": "카탈로그, 패치, 스크립트 작업의 명령어와 결과 이력을 확인합니다.",
    "settings": "OAS·OHS·접속 URL 등 현재 운영 설정값을 조회합니다.",
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
  <script src="/static/app.js" defer></script>
</head>
<body>
  <div class="shell">
    <header class="app-topbar">
      <a class="brand" href="/resources" aria-label="OAS Admin Lite">
        <span class="brand-mark">OAS</span>
        <span class="brand-copy">
          <strong>Admin Lite</strong>
          <small>Oracle Analytics Server 운영 콘솔</small>
        </span>
      </a>
      <nav class="main-nav" aria-label="주요 메뉴">{nav}</nav>
      <div class="top-actions"><span class="status-pill">{auth}</span></div>
    </header>
    <main class="content content-{active}">
      <header class="page-header">
        <div>
          <h1>{title}</h1>
          <p class="page-description">{description}</p>
        </div>
      </header>
      {notice}
      {content}
    </main>
  </div>
</body>
</html>""".format(title=esc(title), nav=nav(active), description=esc(description), auth="Auth Enabled" if auth_enabled else "Local Mode", notice=notice, content=content, active=esc(active))


def nav(active):
    items = [
        ("resources", "Resources", "/resources"),
        ("catalog", "Catalog", "/catalog"),
        ("patch", "Patch", "/patch"),
        ("scripts", "Scripts", "/scripts"),
        ("jobs", "Jobs / Audit", "/jobs"),
        ("settings", "Settings", "/settings"),
    ]
    links = []
    for key, label, href in items:
        attrs = ' class="active" aria-current="page"' if key == active else ''
        links.append('<a{0} href="{1}">{2}</a>'.format(attrs, href, label))
    return "".join(links)

def resources_page(ctx, query):
    snap = ctx.resources.snapshot()
    metric_cards = "".join(metric_card(metric) for metric in snap.metrics)
    readiness = runtime_readiness(snap.service_checks, ctx.cfg)
    oas_rows = "".join(check_row(c, value_second=False) for c in snap.oas_checks)
    resource_rows = "".join(check_row(c, value_second=False) for c in snap.resource_checks)
    content = """
<section class="panel">
  <div class="panel-head"><h2>서버 상태 및 리소스 요약</h2><a class="button secondary" href="/resources">새로고침</a></div>
  <div class="resource-summary-identifiers">{snapshot}</div>
  <div class="resource-summary-divider"></div>
  <h3 class="resource-summary-title">서버 리소스</h3>
  <div class="metric-grid">{metric_cards}</div>
  {legend}
</section>
<section class="panel">
  <div class="panel-head"><div><h2>OAS 서비스 기동 현황</h2><p class="muted">아래 리스너 및 프로세스 상세 점검을 OAS 기동 순서로 요약합니다.</p></div><a class="button secondary" href="/resources">상태 새로고침</a></div>
  {readiness}
</section>
<section class="panel">
  <div class="panel-head"><h2>리스너 및 프로세스 상세</h2></div>
  <table><thead><tr><th>항목</th><th>상태</th><th>값</th><th>상세</th></tr></thead><tbody>{resource_rows}</tbody></table>
</section>
""".format(snapshot=snapshot_kv(snap), legend=metric_status_legend(), metric_cards=metric_cards, readiness=readiness, oas_rows=oas_rows, resource_rows=resource_rows)
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
      <span><strong class="status-word HIGH">HIGH</strong> 리소스 사용률이 높은 상태입니다. 현재 성능 영향 여부는 si/so, I/O wait 등 추가 지표와 함께 확인합니다.</span>
    </div>
    """


def runtime_readiness(checks, cfg):
    by_name = {check.name: check for check in checks}
    access_links = runtime_access_links(cfg)
    stages = [
        ("01", "WebLogic 기반 서비스", "도메인 관리 서비스가 준비되면 관리 콘솔에 접속할 수 있습니다.", ["Node Manager", "WebLogic AdminServer"], access_links["weblogic"]),
        ("02", "OAS Managed Server", "BI Managed Server가 기동되면 OAS 서비스가 초기화됩니다.", ["WebLogic BI Managed Server"], []),
        ("03", "OAS 시스템 컴포넌트", "bitools/bin/status.sh 결과로 obiccs1, obis1, obips1, obijh1, obisch1 상태를 확인합니다.", ["OBICCS (obiccs1)", "OBIS (obis1)", "OBIPS (obips1)", "OBIJH (obijh1)", "OBISCH (obisch1)"], access_links["oas"]),
        ("04", "OAS 접속 경로", "OHS를 통해 Analytics와 Catalog REST API에 접근할 수 있습니다.", ["Catalog REST Endpoint", "OHS HTTP Server"], access_links["gateway"]),
    ]
    access_cards = runtime_access_cards(stages, by_name)
    flow = "".join(runtime_stage(number, title, description, [by_name[name] for name in names if name in by_name]) for number, title, description, names, links in stages)
    return '<div class="runtime-access-grid">{0}</div><div class="runtime-flow">{1}</div>'.format(access_cards, flow)


def runtime_stage(number, title, description, checks):
    ready = len([check for check in checks if check.status == "OK"])
    if checks and ready == len(checks):
        state, status = "준비 완료", "OK"
    elif ready:
        state, status = "기동 중", "WARN"
    else:
        state, status = "시작 대기", "WAIT"
    services = "".join(runtime_service_card(check) for check in checks)
    return """
    <section class="runtime-stage {status}">
      <div class="runtime-stage-head"><span class="runtime-stage-number">{number}</span><div><h3>{title}</h3><p>{description}</p></div><span class="runtime-stage-state">{state}</span></div>
      <div class="runtime-stage-services">{services}</div>
    </section>
    """.format(number=esc(number), title=esc(title), description=esc(description), state=esc(state), status=esc(status), services=services)


def runtime_access_links(cfg):
    oas = cfg.oas
    return {
        "weblogic": [
            ("WebLogic Console", getattr(oas, "weblogic_console_url", "")),
            ("Enterprise Manager", getattr(oas, "enterprise_manager_url", "")),
        ],
        "oas": [("Open Analytics", getattr(oas, "analytics_url", ""))],
        "gateway": [("Catalog 조회 화면", "/catalog")],
    }


def runtime_access_cards(stages, by_name):
    cards = []
    for number, stage_title, description, names, links in stages:
        if not links:
            continue
        checks = [by_name[name] for name in names if name in by_name]
        ready = bool(checks) and len([check for check in checks if check.status == "OK"]) == len(checks)
        for label, url in links:
            href = runtime_link_url(url)
            if ready and href:
                cards.append('<a class="runtime-access-card OK" href="{0}" target="_blank" rel="noopener noreferrer"><span class="runtime-access-signal"></span><span><strong>{1}</strong><small>{2} ready</small></span><em>Open</em></a>'.format(esc(href), esc(label), esc(stage_title)))
            else:
                note = "URL not configured" if not href else "Waiting for service"
                cards.append('<span class="runtime-access-card WAIT"><span class="runtime-access-signal"></span><span><strong>{0}</strong><small>{1}</small></span><em>{2}</em></span>'.format(esc(label), esc(stage_title), esc(note)))
    return "".join(cards)


def runtime_link_url(value):
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    parsed = urlparse(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else ""


def runtime_service_card(check):
    return """
    <div class="runtime-service {status}"><div><strong>{name}</strong><small>{detail}</small></div><div><span>{value}</span>{badge}</div></div>
    """.format(status=esc(check.status), name=esc(check.name), detail=esc(check.detail).replace("\n", " · "), value=esc(check.value), badge=badge(check.status))


def metric_card(metric):
    percent = int(metric.percent or 0)
    if percent < 0:
        percent = 0
    if percent > 100:
        percent = 100
    label_position = "ON_FILL" if percent >= 50 else "OFF_FILL"
    metadata = metric_metadata(metric)
    status_badge = metric_status_badge(metric)
    return """
    <div class="metric-card {status}">
      <div class="metric-head"><span>{name}</span>{badge}</div>
      <div class="metric-value"><strong>{value}</strong><span>{unit}</span></div>
      <div class="meter"><span style="width:{percent}%"></span><b class="meter-label {label_position}" style="left:{percent}%">{percent}%</b></div>
      {metadata}
    </div>
    """.format(status=esc(metric.status), name=esc(metric.name), badge=status_badge, value=esc(metric.value), unit=esc(metric.unit), percent=percent, label_position=label_position, metadata=metadata)


def metric_status_badge(metric):
    if metric.status != "HIGH":
        return badge(metric.status)
    advice = metric_high_guidance(metric.name)
    return '<div class="metric-advice-wrap"><button type="button" class="badge HIGH metric-advice-toggle" data-metric-advice aria-expanded="false">HIGH</button><div class="metric-advice" hidden>{0}</div></div>'.format(esc(advice))


def metric_high_guidance(name):
    if name == "Load":
        return "[확인]\n• top 또는 CPU 상위 프로세스 확인\n• vmstat 1 5에서 I/O wait(wa) 확인\n[조치]\n• 배치·백업·대용량 수집 작업 시간 조정"
    if name == "Memory":
        return "[확인]\n• free -h로 메모리 여유 확인\n• 메모리 상위 OAS/Java 프로세스 확인\n[조치]\n• 불필요한 작업 중지 후 JVM·서버 메모리 증설 검토"
    if name == "Swap":
        return "[확인]\n• free -h와 vmstat 1 5에서 si/so 지속 여부 확인\n• 메모리 상위 프로세스 확인\n[조치]\n• 메모리 사용 작업 조정 또는 증설 검토"
    if name.startswith("Disk "):
        return "[확인]\n• df -h로 대상 파일 시스템 확인\n• du -xhd1 <마운트경로> | sort -h로 큰 경로 확인\n[조치]\n• 보존 정책에 따라 로그·진단 번들·백업 파일 정리"
    return "[확인]\n• 상세 지표와 ps/top 결과로 원인 확인\n[조치]\n• 수집 시각·사용률·실행 작업을 기록해 운영 담당자에게 전달"


def metric_metadata(metric):
    rows = getattr(metric, "metadata", None) or []
    if not rows and metric.detail:
        rows = [("상세", metric.detail)]
    return '<dl class="metric-meta">{0}</dl>'.format("".join("<dt>{0}</dt><dd>{1}</dd>".format(esc(label), esc(value)) for label, value in rows)) if rows else ""

def catalog_page(ctx, query):
    summary = ctx.catalog.last_summary()
    type_rows = summary.get("type_rows") or rows_from_counts(summary.get("counts") or {})
    owner_rows = summary.get("owners") or []
    folder_rows = summary.get("folder_rows") or []
    all_items = summary.get("all_items") or summary.get("items") or []
    items = filtered_catalog_items(summary.get("items") or [], query)
    content = """
<section class="panel compact-panel" id="summary">
  <div class="panel-head">
    <h2>수집 상태</h2>
    <form method="post" action="/catalog/scan"><button type="submit">수집 실행</button></form>
  </div>
  <dl class="kv compact"><dt>Endpoint</dt><dd>{endpoint}</dd><dt>Auth User</dt><dd>{auth_user}</dd><dt>Last Scan</dt><dd>{last_scan}</dd><dt>Status</dt><dd>{status}</dd></dl>
  <div class="catalog-summary">
    {total_card}
    {modified_card}
    {owner_card}
  </div>
</section>
<section class="insight-grid">
  <section class="panel compact-panel">
    <div class="panel-head"><h2>유형별 현황</h2></div>
    {type_chart}
  </section>
  <section class="panel compact-panel">
    <div class="panel-head"><h2>폴더 구조 요약</h2><span class="muted">폴더별 자산 수</span></div>
    {folder_tree}
  </section>
</section>
<section class="panel compact-panel owner-panel">
  {owner_workspace}
</section>
""".format(
        endpoint=esc(summary.get("endpoint", "")),
        auth_user=esc(summary.get("auth_user", "")),
        last_scan=fmt_ts(summary.get("last_scan", 0)),
        status=badge(summary.get("status", "READY")),
        total_card=catalog_card("전체 자산", summary.get("total_assets", 0), "수집된 모든 카탈로그 객체", ""),
        modified_card=catalog_card("최근 30일 변경", summary.get("modified_30_days", 0), "최근 변경된 카탈로그 자산", "warn"),
        owner_card=catalog_card("Owner 수", summary.get("owner_count", 0), "카탈로그 자산을 소유한 사용자", ""),
        type_chart=type_chart(type_rows),
        folder_tree=folder_tree(folder_rows),
        owner_workspace=owner_workspace(owner_rows, all_items, first(query, "owner")),
    )
    return layout(ctx, "Catalog", "catalog", content, query)


def rows_from_counts(counts):
    return [{"type": key, "count": counts[key]} for key in sorted(counts, key=lambda item: counts[item], reverse=True)]


def catalog_card(label, value, detail, extra_class):
    return '<div class="catalog-card {3}"><span>{0}</span><strong>{1}</strong><p>{2}</p></div>'.format(esc(label), esc(value), esc(detail), esc(extra_class))


def type_chart(rows):
    if not rows:
        return '<p class="muted">아직 유형별 수집 결과가 없습니다.</p>'
    max_count = max([int(row.get("count", 0) or 0) for row in rows] + [1])
    body = []
    for row in rows[:10]:
        count = int(row.get("count", 0) or 0)
        percent = int(round((count / float(max_count)) * 100)) if max_count else 0
        body.append('<div class="type-row"><span>{0}</span><div class="bar"><i style="width:{1}%"></i></div><strong>{2}</strong></div>'.format(esc(row.get("type", "unknown")), percent, count))
    return '<div class="type-bars">{0}</div>'.format("".join(body))


def folder_tree(rows):
    if not rows:
        return '<p class="muted">아직 폴더 구조 수집 결과가 없습니다.</p>'
    body = []
    for row in rows[:10]:
        folder = row.get("folder", "unknown")
        body.append('<div>{0} <strong>{1}</strong></div>'.format(esc(folder), esc(row.get("count", 0))))
    return '<div class="folder-tree">{0}</div>'.format("".join(body))


def acl_panel(summary):
    if not summary or not summary.get("checked"):
        return '<p class="muted">ACL은 수집 실행 후 상위 일부 자산을 대상으로 확인합니다.</p>'
    rows = [
        ("Broad write permission", summary.get("broad_write", 0), "넓은 역할에 write/delete/권한관리 권한 존재", "FAILED" if summary.get("broad_write", 0) else "OK"),
        ("Permission management", summary.get("permission_management", 0), "changePermission 또는 takeOwnership 권한 존재", "WARN" if summary.get("permission_management", 0) else "OK"),
        ("ACL fetch failed", summary.get("acl_failed", 0), "권한 부족 또는 API 오류로 ACL 확인 실패", "WARN" if summary.get("acl_failed", 0) else "OK"),
    ]
    body = "".join('<div class="risk-item"><strong>{0} {1}</strong><p class="muted">{2}</p></div>'.format(esc(name), count_badge(status, count), esc(detail)) for name, count, detail, status in rows)
    return '<div class="risk-list">{0}</div>'.format(body)


def owner_table(rows):
    if not rows:
        return '<p class="muted">아직 owner 기준 수집 결과가 없습니다.</p>'
    body = []
    for row in rows[:10]:
        status = "FAILED" if str(row.get("owner", "")).lower() == "unknown" else "WARN" if int(row.get("risk", 0) or 0) else "OK"
        body.append('<tr><td>{0}</td><td>{1}</td><td>{2}</td><td>{3}</td><td>{4}</td></tr>'.format(esc(row.get("owner", "unknown")), esc(row.get("count", 0)), esc(row.get("lastModified", "") or "-"), esc(row.get("folder", "-")), count_badge(status, int(row.get("risk", 0) or 0))))
    return '<table class="owner-table"><thead><tr><th>Owner</th><th>Items</th><th>최근 변경</th><th>주요 폴더</th><th>리스크</th></tr></thead><tbody>{0}</tbody></table>'.format("".join(body))



def owner_workspace(rows, items, selected_owner):
    if not rows:
        return '<p class="muted">\uc218\uc9d1\ub41c owner\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.</p>'
    owners = [row.get("owner", "unknown") for row in rows]
    selected_owner = selected_owner if selected_owner in owners else owners[0]
    links = []
    for row in rows:
        owner = row.get("owner", "unknown")
        active = " active" if owner == selected_owner else ""
        links.append('<a class="owner-choice{0}" href="/catalog?owner={1}"><span>{2}</span><b>{3}</b></a>'.format(active, quote(owner), esc(owner), esc(row.get("count", 0))))
    selected_items = [item for item in items if item.get("owner") == selected_owner]
    selected_items.sort(key=lambda item: item.get("lastModified") or "", reverse=True)
    return '<div class="owner-workspace"><aside class="owner-list"><h2>Owners</h2>{0}</aside><section class="owner-detail"><div class="panel-head"><h2>{1}</h2><span class="muted">{2} items</span></div>{3}</section></div>'.format("".join(links), esc(selected_owner), len(selected_items), owner_detail_table(selected_items))


def owner_detail_table(items):
    if not items:
        return '<p class="muted">\uc120\ud0dd\ud55c owner\uc758 \uc790\uc0b0\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.</p>'
    body = []
    for item in items:
        body.append('<tr><td>{0}</td><td>{1}</td><td>{2}</td><td>{3}</td><td>{4}</td></tr>'.format(esc(item.get("name", "-")), esc(item.get("type", "unknown")), esc(item.get("owner", "unknown")), esc(item.get("lastModified", "") or "-"), esc(item.get("folder", "unknown"))))
    return '<table><thead><tr><th>Name</th><th>Type</th><th>Owner</th><th>Last Modified</th><th>Folder</th></tr></thead><tbody>{0}</tbody></table>'.format("".join(body))


def catalog_filters(summary, query):
    type_value = first(query, "type")
    owner_value = first(query, "owner")
    risk_value = first(query, "risk")
    folder_value = first(query, "folder")
    type_options = ['<option value="">All</option>']
    for row in summary.get("type_rows") or []:
        value = row.get("type", "")
        type_options.append('<option value="{0}"{1}>{0}</option>'.format(esc(value), selected(value, type_value)))
    owner_options = ['<option value="">All owners</option>']
    for row in summary.get("owners") or []:
        value = row.get("owner", "")
        owner_options.append('<option value="{0}"{1}>{0}</option>'.format(esc(value), selected(value, owner_value)))
    return """
  <form method="get" action="/catalog" class="filter-grid">
    <label>Type<select name="type">{type_options}</select></label>
    <label>Owner<select name="owner">{owner_options}</select></label>
    <label>Folder<input name="folder" value="{folder_value}" placeholder="/shared/Finance"></label>
    <label>ACL Risk<select name="risk"><option value=""{risk_all}>All</option><option value="WARN"{risk_warn}>WARN/FAILED</option><option value="FAILED"{risk_failed}>FAILED only</option></select></label>
    <label>Apply<button type="submit" class="secondary">필터 적용</button></label>
  </form>
""".format(type_options="".join(type_options), owner_options="".join(owner_options), folder_value=esc(folder_value), risk_all=selected("", risk_value), risk_warn=selected("WARN", risk_value), risk_failed=selected("FAILED", risk_value))


def filtered_catalog_items(items, query):
    type_value = first(query, "type")
    owner_value = first(query, "owner")
    risk_value = first(query, "risk")
    folder_value = first(query, "folder").lower()
    result = []
    for item in items:
        if type_value and item.get("type") != type_value:
            continue
        if owner_value and item.get("owner") != owner_value:
            continue
        if folder_value and folder_value not in (item.get("folder", "") or "").lower():
            continue
        if risk_value == "FAILED" and item.get("aclRisk") != "FAILED":
            continue
        if risk_value == "WARN" and item.get("aclRisk") not in ("WARN", "FAILED"):
            continue
        result.append(item)
    return result


def catalog_detail_table(items):
    if not items:
        return '<p class="muted">표시할 catalog item이 없습니다. 수집 실행 또는 필터 조건을 확인하세요.</p>'
    body = []
    for item in items:
        risk = item.get("aclRisk") or "UNKNOWN"
        badge_status = risk if risk in ("OK", "WARN", "FAILED") else "WARN"
        body.append('<tr><td>{name}</td><td>{type}</td><td>{owner}</td><td>{modified}</td><td>{folder}</td><td><span class="acl"><code>{acl}</code></span></td><td>{risk}</td></tr>'.format(name=esc(item.get("name", "-")), type=esc(item.get("type", "unknown")), owner=esc(item.get("owner", "unknown")), modified=esc(item.get("lastModified", "") or "-"), folder=esc(item.get("folder", "unknown")), acl=esc(item.get("aclSummary", "ACL 미조회")), risk=badge(badge_status)))
    return '<table><thead><tr><th>Name</th><th>Type</th><th>Owner</th><th>Last Modified</th><th>Folder</th><th>ACL</th><th>Risk</th></tr></thead><tbody>{0}</tbody></table>'.format("".join(body))


def selected(value, actual):
    return ' selected' if str(value) == str(actual) else ''

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
    actions = script_actions(state)
    if not actions:
        content = '<section class="panel"><h2>작업 선택</h2><p class="muted">허용된 스크립트가 없습니다. Settings 또는 app.yaml의 scripts.allowed 값을 확인하세요.</p></section>'
        return layout(ctx, "Scripts", "scripts", content, query)
    selected = selected_script(query, actions)
    saved_form = script_saved_form(ctx, selected["script"])
    command = script_last_command(state, selected["script"])
    preview_ready = bool(command) and script_preview_matches(ctx.store.get_json("script_preview_state", {}), saved_form)
    last_job_type = state.get("last_job_type", "")
    recent_output = state.get("last_output", "") if command and last_job_type == "script_run" else ""
    content = """
<div class="script-workbench">
  <section class="panel script-info-panel">
    <div class="panel-head script-picker-head">
      <div><h2>{label}</h2><p class="muted">{purpose}</p></div>
    </div>
    {picker}
    {brief}
  </section>
  <section class="panel script-run-panel">
    <div class="panel-head"><h2>실행 단계</h2><span class="tag danger-tag">실제 실행 주의</span></div>
    <form method="post" class="script-exec-form">
      <input type="hidden" name="script" value="{script}">
      <input type="hidden" name="arg_mode" value="{mode}">
      <section class="script-step input-step">
        <div class="script-step-top"><div class="script-step-head"><span class="step-number">1</span><div><h3>파라미터 입력</h3></div></div><div class="actions script-step-actions"><button formaction="/scripts/preview" type="submit" class="secondary">명령어 미리보기</button></div></div>
        <div class="script-form-grid">{fields}</div>
      </section>
      <section class="script-step command-step">
        <div class="script-step-top"><div class="script-step-head"><span class="step-number">2</span><div><h3>명령어 확인 및 실행</h3><p>쉘 스크립트와 입력 파라미터가 합쳐진 명령어를 확인한 뒤 실행합니다.</p></div></div><div class="actions script-step-actions"><button formaction="/scripts/run" type="submit" class="danger" data-running-label="실행 중..."{run_disabled}>실행</button></div></div>
        {command_box}
        <div class="script-running-status" data-script-running-status role="status" aria-live="polite" hidden>스크립트를 실행 중입니다. 완료될 때까지 기다려 주세요.</div>
        {recent_result}
      </section>
    </form>
  </section>
</div>
""".format(
        picker=script_picker(actions, selected["script"]),
        label=esc(selected["label"]),
        purpose=esc(selected.get("purpose", "")),
        script=esc(selected["script"]),
        mode=esc(selected["mode"]),
        brief=script_brief(selected, state.get("bitools_bin", "")),
        fields=script_fields(selected, saved_form),
        command_box=script_command_box(command, last_job_type, selected["mode"]),
        run_disabled="" if preview_ready else ' disabled aria-disabled="true" title="명령어 미리보기를 먼저 수행하세요."',
        recent_result=script_recent_result(recent_output),
    )
    return layout(ctx, "Scripts", "scripts", content, query)

def script_actions(state):
    allowed = set(state.get("allowed") or [])
    return [item for item in SCRIPT_ACTIONS if item["script"] in allowed]


def selected_script(query, actions):
    requested = first(query, "script")
    for item in actions:
        if item["script"] == requested:
            return item
    return actions[0]


def script_saved_form(ctx, script):
    saved = ctx.store.get_json("script_form_state", {})
    if saved.get("script") != script:
        return {}
    return saved


def script_form_state(form):
    return {
        "script": first(form, "script"),
        "arg_mode": first(form, "arg_mode"),
        "service_instance": first(form, "service_instance"),
        "export_dir": first(form, "export_dir"),
        "export_options": first(form, "export_options"),
        "stdin_file": first(form, "stdin_file"),
        "diagnostic_zip": first(form, "diagnostic_zip"),
        "report_output": first(form, "report_output"), "report_type": first(form, "report_type"), "report_fields": first(form, "report_fields"), "report_folder": first(form, "report_folder"), "report_depth": first(form, "report_depth"), "report_skip_types": first(form, "report_skip_types"), "report_skip_folders": first(form, "report_skip_folders"), "report_delimiter": first(form, "report_delimiter"), "report_distinct": first(form, "report_distinct"), "report_excel": first(form, "report_excel"), "report_force": first(form, "report_force"), "report_online": first(form, "report_online"), "report_credentials": first(form, "report_credentials"),
    }


def script_preview_matches(preview_state, form_state):
    return bool(preview_state) and preview_state == form_state

def script_last_command(state, script):
    command = state.get("last_command", "")
    if command and script in command:
        return command
    return ""


def script_brief(action, bitools_bin):
    optional_block = ''
    if action.get("mode") != "diagnostic":
        optional_block = '<section class="script-info-block optional-parameters"><h3>옵션 파라미터</h3>{0}</section>'.format(script_list(action.get("optional") or []))
    return """
    <div class="script-brief">
      <section class="script-info-block"><h3>실행 구문 형식</h3><code class="script-method-path">{bitools}</code><pre>{method}</pre></section>
      <section class="script-info-block required-parameters"><h3>필수 파라미터</h3>{required}</section>
      {optional_block}
      <section class="script-info-block caution"><h3>주의사항</h3>{cautions}</section>
    </div>
    """.format(
        bitools=esc(bitools_bin),
        method=esc(action.get("method", "")),
        required=script_list(action.get("required") or []),
        optional_block=optional_block,
        cautions=script_list(action.get("cautions") or []),
    )
def script_list(items):
    if not items:
        return '<p class="muted">-</p>'
    return '<ul>{0}</ul>'.format("".join('<li>{0}</li>'.format(esc(item)) for item in items))


def script_command_box(command, last_job_type="", mode=""):
    if last_job_type == "script_run":
        help_text = "최근 실제 실행에 사용한 명령어입니다. 입력값을 바꾸면 1단계에서 다시 입력 완료(명령어 확인)를 눌러 확인하세요."
    elif command:
        help_text = "미리보기로 생성된 명령어입니다. 이 단계에서는 서버에서 실행하지 않았습니다."
    else:
        help_text = "1단계의 입력 완료(명령어 확인) 버튼을 누르면 쉘 스크립트와 파라미터가 합쳐진 명령어가 표시됩니다."
    return '<label class="full command-preview"><textarea readonly placeholder="명령어 확인 버튼을 누르면 실행될 명령어가 여기에 표시됩니다.">{0}</textarea><span class="field-help">{1}</span></label>'.format(esc(command), esc(help_text))

def script_recent_result(output):
    if not output:
        return '<div class="result script-result empty"><h3>최근 실제 실행 결과</h3><p class="muted">아직 실제 실행 결과가 없습니다. 2단계에서 실행한 뒤 stdout/stderr와 exit code를 확인하세요.</p></div>'
    return '<div class="result script-result"><h3>최근 실제 실행 결과</h3><pre>{0}</pre></div>'.format(esc(output))

def script_picker(actions, selected):
    buttons = []
    for item in actions:
        active = " active" if item["script"] == selected else ""
        buttons.append('<button class="script-option{active}" type="submit" name="script" value="{script}"><strong>{label}</strong><span>{script}</span></button>'.format(active=active, script=esc(item["script"]), label=esc(item.get("short_label", item["label"]))))
    return '<form method="get" action="/scripts" class="script-picker">{0}</form>'.format("".join(buttons))


def catalog_report_examples():
    return """
    <dialog class="report-example-dialog" data-report-example-dialog aria-label="\uce74\ud0c8\ub85c\uadf8 \uac10\uc0ac \ubcf4\uace0\uc11c \uc608\uc2dc">
      <div class="report-example-head"><div><h3>\ubcf4\uace0\uc11c \uc608\uc2dc</h3><p>\uc120\ud0dd \ud6c4 \uc785\ub825\uac12\uc744 \ud544\uc694\uc5d0 \ub9de\uac8c \uc218\uc815\ud558\uc138\uc694.</p></div><button type="button" class="secondary" data-report-example-close>\ub2eb\uae30</button></div>
      <div class="report-example-list">
        <button type="button" class="report-example-choice" data-report-example data-report-output="/u01/oas-admin-lite/backups/catalog-analysis.csv" data-report-type="Analysis" data-report-fields="Name:Path:Owner:Subject Area:Table:Column" data-report-folder="/shared"><strong>Analysis \uc790\uc0b0 \ubaa9\ub85d</strong><span>Name, Path, Owner, Subject Area, Table, Column</span></button>
        <button type="button" class="report-example-choice" data-report-example data-report-output="/u01/oas-admin-lite/backups/catalog-dashboard.csv" data-report-type="Dashboard" data-report-fields="Name:Path:Owner:Dashboard Style" data-report-folder="/shared"><strong>Dashboard \uc790\uc0b0 \ubaa9\ub85d</strong><span>Name, Path, Owner, Dashboard Style</span></button>
        <button type="button" class="report-example-choice" data-report-example data-report-output="/u01/oas-admin-lite/backups/catalog-privileges.csv" data-report-type="Security ACL" data-report-fields="Path:Privilege" data-report-folder="/system/privs"><strong>\uad8c\ud55c \ubaa9\ub85d</strong><span>Security ACL, Path, Privilege</span></button>
        <button type="button" class="report-example-choice" data-report-example data-report-output="/u01/oas-admin-lite/backups/catalog-acl.csv" data-report-type="All" data-report-fields="Path:ACL" data-report-folder="/shared"><strong>ACL \uac10\uc0ac</strong><span>All objects, Path, ACL</span></button>
      </div>
    </dialog>
    """


def script_fields(action, values=None):
    values = values or {}
    if action["mode"] == "exportarchive":
        return """
        <label class="service-instance-field">Service instance key<input name="service_instance" value="{service_instance}" placeholder="ssi"></label>
        <label class="export-dir-field">Export directory<input name="export_dir" value="{export_dir}" placeholder="/u01/oas-admin-lite/backups/export"></label>
        <label class="full">Encryption password file path<input name="stdin_file" value="{stdin_file}" placeholder="/u01/oas-admin-lite/backups/exportpwd.txt"><span class="field-help">사전에 생성한 password 파일 경로를 입력합니다. 웹앱은 파일 내용을 읽지 않고, 실행 명령에 &lt; 파일경로 형태로 붙여 실행합니다.</span></label>
        <label class="full">Optional parameters<input name="export_options" value="{export_options}" placeholder="noconnectionparams nouserfolders includedata advancedoptions=/path/options.json"></label>
        """.format(service_instance=esc(values.get("service_instance", "")), export_dir=esc(values.get("export_dir", "")), export_options=esc(values.get("export_options", "")), stdin_file=esc(values.get("stdin_file", "")))
    if action["mode"] == "diagnostic":
        return """
        <label class="full">ZIP file name<input name="diagnostic_zip" value="{diagnostic_zip}" placeholder="/u01/oas-admin-lite/bundles/oas-diagnostic.zip"></label>
        <p class="muted full">ZIP 파일명은 /u01/oas-admin-lite/bundles 기준으로 입력합니다. 실제 실행 시 diagnostic_dump.sh &lt;zip file name&gt; 형식으로 서버에서 실행됩니다.</p>
        """.format(diagnostic_zip=esc(values.get("diagnostic_zip", "")))
    if action["mode"] == "catalog_report":
        return catalog_report_examples() + """
        <section class="report-field-section report-required full"><div class="report-field-grid">
          <label class="report-output">Report output file<input name="report_output" value="{output}" placeholder="/u01/oas-admin-lite/backups/catalog-audit.csv"></label><label class="report-type">Object type<select name="report_type">{types}</select></label><label class="report-fields">Report fields<input name="report_fields" value="{fields}" placeholder="Name:Path:Owner:Modified:ACL"></label><label class="report-online">OBIPS URL<input name="report_online" value="{online}" placeholder="https://oas.example.com/analytics/saw.dll"></label><label class="report-credentials">Credentials properties file<input name="report_credentials" value="{credentials}" placeholder="/u01/oas-admin-lite/data/catmancredentials.properties"></label>
        </div></section>
        <section class="report-field-section report-optional full"><div class="report-field-grid">
          <label class="report-folder">Catalog folder<input name="report_folder" value="{folder}" placeholder="/shared"></label><label class="report-depth">Recursion depth<input name="report_depth" value="{depth}" inputmode="numeric"></label><label class="report-skip-types">Skip types<input name="report_skip_types" value="{skip_types}" placeholder="Action:Favorites List"></label><label class="report-skip-folders">Skip folders<input name="report_skip_folders" value="{skip_folders}"></label><label class="report-delimiter">Delimiter<input name="report_delimiter" value="{delimiter}" placeholder="Default: tab"></label>
          <div class="report-options full"><label class="checkbox-field"><input type="checkbox" name="report_distinct" value="1"{distinct}> Remove duplicates (-distinct)</label><label class="checkbox-field"><input type="checkbox" name="report_excel" value="1"{excel}> Excel CSV (-excelFormat)</label><label class="checkbox-field"><input type="checkbox" name="report_force" value="1"{force}> Replace output (-forceoutputFile)</label></div>
        </div></section>
        """.format(output=esc(values.get("report_output", "")), types=report_type_options(values.get("report_type", "All")), fields=esc(values.get("report_fields", "Name:Path:Owner:Modified:ACL")), folder=esc(values.get("report_folder", "/shared")), depth=esc(values.get("report_depth", "")), skip_types=esc(values.get("report_skip_types", "")), skip_folders=esc(values.get("report_skip_folders", "")), delimiter=esc(values.get("report_delimiter", "")), online=esc(values.get("report_online", "")), credentials=esc(values.get("report_credentials", "")), distinct=checked(values, "report_distinct"), excel=checked(values, "report_excel"), force=checked(values, "report_force"))
    return '<label class="full">Arguments<input name="args" placeholder="help 출력에서 확인한 옵션 입력"></label>'


def script_request(form):
    script = first(form, "script")
    mode = first(form, "arg_mode")
    stdin_text = ""
    stdin_label = ""
    if mode == "exportarchive":
        service_instance = required(form, "service_instance", "Service instance key")
        export_dir = required(form, "export_dir", "Export directory")
        export_options = first(form, "export_options")
        validate_export_options(export_options)
        stdin_label = required(form, "stdin_file", "Encryption password file path")
        raw_args = join_args([service_instance, export_dir], export_options)
    elif mode == "diagnostic":
        zip_name = required(form, "diagnostic_zip", "ZIP file name")
        raw_args = join_args([zip_name])
    elif mode == "catalog_report":
        if script != "runcat.sh":
            raise ValueError("Catalog report mode is only available for runcat.sh")
        raw_args = catalog_report_args(form)
    else:
        if script == "runcat.sh":
            raise ValueError("runcat.sh must use the Catalog report form")
        raw_args = first(form, "args")
    return script, raw_args, stdin_text, stdin_label


def required(form, key, label):
    value = first(form, key).strip()
    if not value:
        raise ValueError("{0} 값을 입력해야 합니다.".format(label))
    return value


def validate_export_options(value):
    if "encryptionpassword" in (value or "").lower():
        raise ValueError("Optional parameters에는 encryptionpassword를 입력하지 마세요. Password file path로 지정한 파일을 stdin으로 전달합니다.")


REPORT_TYPES = ("All", "Analysis", "Dashboard", "Dashboard Prompt", "Filter", "Agent", "Security ACL", "Workbook", "Accounts", "Folder")
REPORT_FIELDS = {"Created", "Creator", "Modified", "Modifier", "Owner", "ACL", "Description", "Signature", "Folder", "Name", "Path", "Description ID", "Size", "Caption ID", "Tag", "Properties", "Internal Properties", "Content State", "Matching Accounts", "Account Name", "Account GUID", "Account Display Name", "Account Type", "Account Status", "Must Have Permission", "Must NOT Have Permission", "Group Members", "Formula", "Table", "Column", "Subject Area", "SAW Variables", "RPD Variables", "Session Variables", "SQL", "Missing Column IDs", "Unused Column IDs", "Dashboard Style", "Email", "Privilege", "XML", "References"}

def catalog_report_args(form):
    output_file = required(form, "report_output", "Report output file"); report_type = required(form, "report_type", "Object type"); fields = required(form, "report_fields", "Report fields"); online = required(form, "report_online", "OBIPS URL"); credentials = required(form, "report_credentials", "Credentials properties file")
    if report_type not in REPORT_TYPES: raise ValueError("Unsupported catalog report type")
    validate_report_fields(fields)
    if not online.lower().startswith(("http://", "https://")): raise ValueError("OBIPS URL must start with http:// or https://")
    depth = first(form, "report_depth").strip()
    if depth and (not depth.isdigit() or int(depth) < 1): raise ValueError("Recursion depth must be a positive integer")
    args = ["-cmd", "report", "-forceoutputFile" if first(form, "report_force") == "1" else "-outputFile", output_file, "-type", report_type, "-fields", fields]
    for option, key in (("-folder", "report_folder"), ("-recursionDepth", "report_depth"), ("-skipTypes", "report_skip_types"), ("-skipFolder", "report_skip_folders"), ("-delimiter", "report_delimiter")):
        value = first(form, key).strip()
        if value: args.extend([option, value])
    if first(form, "report_distinct") == "1": args.append("-distinct")
    if first(form, "report_excel") == "1": args.append("-excelFormat")
    args.extend(["-online", online, "-credentials", credentials])
    return " ".join(shlex.quote(item) for item in args)

def validate_report_fields(value):
    fields = [item.strip() for item in value.split(":") if item.strip()]
    if not fields or any(item not in REPORT_FIELDS for item in fields): raise ValueError("Report fields must be colon-separated values supported by runcat report")

def report_type_options(selected):
    return "".join('<option value="{0}"{1}>{0}</option>'.format(esc(item), " selected" if item == selected else "") for item in REPORT_TYPES)

def checked(values, key):
    return " checked" if values.get(key) == "1" else ""

def join_args(required_parts, optional_text=""):
    parts = [shlex.quote(item.strip()) for item in required_parts if item and item.strip()]
    if optional_text and optional_text.strip():
        parts.append(optional_text.strip())
    return " ".join(parts)


def script_redirect(script):
    if not script:
        return "/scripts"
    return "/scripts?script={0}".format(quote(script))

def jobs_page(ctx, query):
    rows = "".join(job_row(job) for job in ctx.store.list(100)) or '<tr><td colspan="5">작업 이력이 없습니다.</td></tr>'
    content = '<section class="panel"><h2>작업 이력</h2><table><thead><tr><th>시간</th><th>작업</th><th>명령</th><th>결과</th><th>메시지</th></tr></thead><tbody>{0}</tbody></table></section>'.format(rows)
    return layout(ctx, "Jobs / Audit", "jobs", content, query)


def settings_page(ctx, query):
    cfg = ctx.cfg
    app_values = [
        ("Listen", cfg.server.listen),
        ("Root", cfg.paths.root),
        ("Data", cfg.paths.data_dir),
        ("Logs", cfg.paths.log_dir),
        ("Backups", cfg.paths.backup_dir),
        ("Bundles", cfg.paths.bundle_dir),
        ("Packages", cfg.paths.package_dir),
        ("Allowed Scripts", ", ".join(allowed_scripts(cfg.scripts.allowed))),
        ("Patch Directories", ", ".join(cfg.patch.allowed_patch_dirs)),
        ("Auth User", cfg.security.username),
        ("Auth Password", "configured" if cfg.security.password_sha256 else ""),
    ]
    oas_values = [
        ("ORACLE_HOME", cfg.oas.oracle_home),
        ("DOMAIN_HOME", cfg.oas.domain_home),
        ("bitools/bin", cfg.oas.bitools_bin),
        ("Analytics URL", cfg.oas.analytics_url),
        ("WebLogic Console URL", getattr(cfg.oas, "weblogic_console_url", "")),
        ("Enterprise Manager URL", getattr(cfg.oas, "enterprise_manager_url", "")),
        ("Catalog Base URL", getattr(cfg.oas, "catalog_base_url", "")),
        ("Catalog API Path", getattr(cfg.oas, "catalog_api_path", "")),
        ("Catalog API URL", getattr(cfg.oas, "catalog_api_url", "")),
        ("Catalog Username", getattr(cfg.oas, "catalog_username", "")),
        ("Catalog Password", "configured" if getattr(cfg.oas, "catalog_password", "") else ""),
    ]
    ohs = getattr(cfg, "ohs", None)
    ohs_values = [
        ("OHS ORACLE_HOME", getattr(ohs, "oracle_home", "")),
        ("OHS DOMAIN_HOME", getattr(ohs, "domain_home", "")),
        ("Instance", getattr(ohs, "instance_name", "")),
        ("HTTP Port", getattr(ohs, "http_port", "")),
        ("HTTPS Port", getattr(ohs, "https_port", "")),
    ]
    content = """
<div class="settings-sections">
  {oas_group}
  {ohs_group}
  {app_group}
</div>
<section class="panel settings-note">
  <h2>설정 변경 위치</h2>
  <p class="muted">이 화면에서는 값을 수정하지 않습니다. 배포 서버의 /u01/oas-admin-lite/app/config/app.yaml 또는 실행 환경변수에서 변경한 뒤 앱을 재시작하세요.</p>
</section>
""".format(
        app_group=settings_group("WEB APP", "모니터링 웹앱", "앱 실행 주소, 데이터 저장소, 로그, 백업, 패키지, 작업 허용 정책입니다. OAS 서버 상태가 아니라 oas-admin-lite 운영 값입니다.", app_values, "webapp"),
        oas_group=settings_group("OAS", "Oracle Analytics Server", "OAS 도메인, Oracle Home, bitools, Analytics/Catalog REST 연결 값입니다. Catalog 수집과 스크립트 실행에서 사용합니다.", oas_values, "oas"),
        ohs_group=settings_group("OHS", "Oracle HTTP Server", "OHS 홈, 도메인, 인스턴스, HTTP/HTTPS 포트 설정입니다. Listener 감지 결과와 함께 확인합니다.", ohs_values, "ohs"),
    )
    return layout(ctx, "Settings", "settings", content, query)


def settings_group(kicker, title, description, values, tone):
    rows = "".join("<dt>{0}</dt><dd>{1}</dd>".format(esc(k), esc(v)) for k, v in values)
    return """
<section class="panel settings-group {tone}">
  <div class="settings-heading">
    <span class="settings-kicker">{kicker}</span>
    <h2>{title}</h2>
  </div>
  <p class="muted settings-copy">{description}</p>
  <dl class="kv settings-kv">{rows}</dl>
</section>
""".format(tone=esc(tone), kicker=esc(kicker), title=esc(title), description=esc(description), rows=rows)


def snapshot_kv(snap):
    return '<dl class="kv compact"><dt>Hostname</dt><dd>{0}</dd><dt>OS</dt><dd>{1} / {2}</dd><dt>Checked</dt><dd>{3}</dd></dl>'.format(esc(snap.hostname), esc(snap.os_name), esc(snap.arch), fmt_ts(snap.checked_at))


def check_row(check, value_second=True):
    detail = esc(check.detail).replace("\n", "<br>")
    if value_second:
        cells = "<td>{0}</td><td><pre>{1}</pre></td><td>{2}</td><td>{3}</td>".format(esc(check.name), esc(check.value), badge(check.status), detail)
    else:
        cells = "<td>{0}</td><td>{1}</td><td><pre>{2}</pre></td><td>{3}</td>".format(esc(check.name), badge(check.status), esc(check.value), detail)
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


def count_badge(status, value):
    return '<span class="badge {0}">{1}</span>'.format(esc(status), esc(value))


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
