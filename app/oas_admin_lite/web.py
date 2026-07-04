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
                    script, args, stdin_text = script_request(form)
                    ctx.scripts.preview(script, args, stdin_text)
                    self._redirect_flash(script_redirect(script), "스크립트 실행 명령 Preview가 생성되었습니다.")
                elif parsed.path == "/scripts/run":
                    script, args, stdin_text = script_request(form)
                    if first(form, "confirm") != "RUN":
                        raise ValueError("실행하려면 확인 입력란에 RUN을 입력해야 합니다.")
                    ctx.scripts.run(script, args, stdin_text)
                    self._redirect_flash(script_redirect(script), "스크립트 실행이 완료되었습니다.")
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
        "label": "환경 메타데이터 BAR 내보내기",
        "method": "exportarchive.sh <service instance key> <export directory> {optional parameters}\n\nMandatory: service instance key, export directory, encryption password(stdin)\nOptional examples: noconnectionparams, nouserfolders, noconfiguration, nojobs, includedata, includecustommaps, includedaytodaymetadata, nojazn, nodatamodel, nocontentdatasets, advancedoptions=<json path>\n\nOracle Security guideline: encryption password is not passed on the command line. OAS Admin Lite sends it through stdin and does not store it in the command history.",
    },
    {
        "script": "diagnostic_dump.sh",
        "mode": "raw",
        "label": "Oracle Support 진단 번들 수집",
        "method": "diagnostic_dump.sh -help\n\n실행 시 BI Diagnostic Dump 버전, oa_platform/WebLogic 버전, exp_jazn-data.xml 덤프 경로, dumpsecuritystores.log 등 진단 경로가 출력됩니다. 출력에 표시된 로그와 덤프 파일을 지원 요청 자료로 사용합니다.",
    },
]

PAGE_DESCRIPTIONS = {
    "resources": "OAS 서버의 CPU, Memory, Swap, /u01 Disk, Listener, Process 상태와 주요 런타임 경로를 확인합니다. 앱 설정값은 Settings에서 확인합니다.",
    "catalog": "OAS REST API 수집 결과로 카탈로그 유형, 소유자, 변경일, 폴더 구조, ACL 리스크를 확인합니다.",
    "patch": "현재 ORACLE_HOME의 OPatch inventory를 조회해 설치된 패치 레벨을 확인합니다. 이 화면은 조회 전용이며 패치를 적용하지 않습니다.",
    "scripts": "OAS service instance 관리 스크립트를 작업 버튼으로 선택하고, 실행 방법 확인 후 매개변수를 입력해 Preview 또는 실행합니다.",
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
    <main class="content">
      <header class="page-header">
        <div>
          <p class="eyebrow">OAS Admin Lite</p>
          <h1>{title}</h1>
          <p class="page-description">{description}</p>
          <p class="page-path">{root}</p>
        </div>
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
    links = []
    for key, label, href in items:
        attrs = ' class="active" aria-current="page"' if key == active else ''
        links.append('<a{0} href="{1}">{2}</a>'.format(attrs, href, label))
    return "".join(links)

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
    type_rows = summary.get("type_rows") or rows_from_counts(summary.get("counts") or {})
    owner_rows = summary.get("owners") or []
    folder_rows = summary.get("folder_rows") or []
    items = filtered_catalog_items(summary.get("items") or [], query)
    acl_summary = summary.get("acl_summary") or {}
    content = """
<section class="panel compact-panel" id="summary">
  <div class="panel-head">
    <h2>수집 상태</h2>
    <form method="post" action="/catalog/scan"><button type="submit">수집 실행</button></form>
  </div>
  <dl class="kv compact"><dt>Endpoint</dt><dd>{endpoint}</dd><dt>Auth User</dt><dd>{auth_user}</dd><dt>Last Scan</dt><dd>{last_scan}</dd><dt>Status</dt><dd>{status}</dd><dt>HTTP</dt><dd>{http_status}</dd><dt>Message</dt><dd>{message}</dd></dl>
  <div class="catalog-summary">
    {total_card}
    {modified_card}
    {owner_card}
    {acl_card}
  </div>
</section>
<section class="insight-grid">
  <section class="panel compact-panel">
    <div class="panel-head"><h2>유형별 현황</h2></div>
    {type_chart}
  </section>
  <section class="panel compact-panel">
    <div class="panel-head"><h2>폴더 구조 요약</h2></div>
    {folder_tree}
  </section>
  <section class="panel compact-panel">
    <div class="panel-head"><h2>ACL 리스크</h2></div>
    {acl_panel}
  </section>
</section>
<section class="panel compact-panel owner-panel">
  <div class="panel-head"><h2>Owner Top 10</h2></div>
  {owner_table}
</section>
<section class="panel" id="detail">
  <div class="panel-head"><h2>Catalog Detail</h2><span class="muted">최대 {detail_limit}개 표시</span></div>
  {filters}
  {detail_table}
</section>
""".format(
        endpoint=esc(summary.get("endpoint", "")),
        auth_user=esc(summary.get("auth_user", "")),
        last_scan=fmt_ts(summary.get("last_scan", 0)),
        status=badge(summary.get("status", "READY")),
        http_status=esc(summary.get("http_status", "")),
        message=esc(summary.get("message", "")),
        total_card=catalog_card("Total Assets", summary.get("total_assets", 0), "수집된 catalog item 전체", ""),
        modified_card=catalog_card("Modified 30 Days", summary.get("modified_30_days", 0), "최근 변경된 이관 영향 후보", "warn"),
        owner_card=catalog_card("Owner Count", summary.get("owner_count", 0), "고유 owner 수", ""),
        acl_card=catalog_card("ACL Risks", acl_summary.get("risk_total", 0), "ACL checked {0}/{1}".format(acl_summary.get("checked", 0), acl_summary.get("eligible", 0)), "risk"),
        type_chart=type_chart(type_rows),
        folder_tree=folder_tree(folder_rows),
        acl_panel=acl_panel(acl_summary),
        owner_table=owner_table(owner_rows),
        filters=catalog_filters(summary, query),
        detail_table=catalog_detail_table(items),
        detail_limit=esc((summary.get("limits") or {}).get("detail_limit", 100)),
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
    content = """
<section class="panel">
  <div class="panel-head"><h2>작업 선택</h2><span class="muted">{bitools}</span></div>
  {picker}
</section>
<section class="panel script-run-panel">
  <div class="panel-head"><h2>{label}</h2><span class="tag">{script}</span></div>
  <div class="script-run-layout">
    <div class="command-help"><h3>실행 명령어 방법</h3><pre>{method}</pre></div>
    <form method="post" class="script-exec-form">
      <input type="hidden" name="script" value="{script}">
      <input type="hidden" name="arg_mode" value="{mode}">
      <div class="script-form-grid">{fields}</div>
      <div class="actions"><button formaction="/scripts/preview" type="submit" class="secondary">명령어 확인</button></div>
      <label class="full">실행 확인<input name="confirm" placeholder="RUN"></label>
      <button formaction="/scripts/run" type="submit" class="danger">실행</button>
    </form>
  </div>
  {result}
</section>
""".format(
        bitools=esc(state.get("bitools_bin", "")),
        picker=script_picker(actions, selected["script"]),
        label=esc(selected["label"]),
        script=esc(selected["script"]),
        method=esc(selected["method"]),
        mode=esc(selected["mode"]),
        fields=script_fields(selected),
        result=result_block(state.get("last_command", ""), state.get("last_output", "")),
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


def script_picker(actions, selected):
    buttons = []
    for item in actions:
        active = " active" if item["script"] == selected else ""
        buttons.append('<button class="script-option{active}" type="submit" name="script" value="{script}"><strong>{label}</strong><span>{script}</span></button>'.format(active=active, script=esc(item["script"]), label=esc(item["label"])))
    return '<form method="get" action="/scripts" class="script-picker">{0}</form>'.format("".join(buttons))


def script_fields(action):
    if action["mode"] == "exportarchive":
        return """
        <label>Service instance key<input name="service_instance" placeholder="ssi"></label>
        <label>Export directory<input name="export_dir" placeholder="/u01/oas-admin-lite/backups/export"></label>
        <label class="full">Optional parameters<input name="export_options" placeholder="noconnectionparams nouserfolders includedata advancedoptions=/path/options.json"></label>
        <label class="full">Encryption password(stdin)<input type="password" name="stdin_text" autocomplete="new-password" placeholder="명령어 이력에 남기지 않고 stdin으로 전달"></label>
        """
    return '<label class="full">Arguments<input name="args" placeholder="help 출력에서 확인한 옵션 입력"></label>'


def script_request(form):
    script = first(form, "script")
    mode = first(form, "arg_mode")
    stdin_text = first(form, "stdin_text")
    if mode == "exportarchive":
        service_instance = required(form, "service_instance", "Service instance key")
        export_dir = required(form, "export_dir", "Export directory")
        stdin_text = required(form, "stdin_text", "Encryption password(stdin)")
        raw_args = join_args([service_instance, export_dir], first(form, "export_options"))
    else:
        raw_args = first(form, "args")
    return script, raw_args, stdin_text


def required(form, key, label):
    value = first(form, key).strip()
    if not value:
        raise ValueError("{0} 값을 입력해야 합니다.".format(label))
    return value


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
