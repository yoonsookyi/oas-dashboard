import base64
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse

from .storage import Job


TYPE_KEYS = ("type", "objectType", "itemType", "resourceType", "catalogObjectType", "contentType")
ID_KEYS = ("id", "catalogId", "objectId")
NAME_KEYS = ("name", "displayName", "title")
OWNER_KEYS = ("owner", "createdBy", "ownerName")
LAST_MODIFIED_KEYS = ("lastModified", "modified", "updated", "lastModifiedDate")
PARENT_KEYS = ("parentId", "parent", "parentName")
PATH_KEYS = ("path", "catalogPath", "location", "absolutePath")

TYPE_PAGE_LIMIT = 500
TYPE_PAGE_MAX = 3
ACL_CHECK_LIMIT = 50
DETAIL_LIMIT = 100
TOP_LIMIT = 10
BROAD_ACCOUNTS = ("authenticated", "everyone", "biconsumer", "bi consumer", "consumer")
RISK_PERMISSIONS = ("write", "delete", "changePermission", "takeOwnership")
MANAGE_PERMISSIONS = ("changePermission", "takeOwnership")


class CatalogSummary(object):
    def __init__(self, endpoint, last_scan=0.0, counts=None, message="아직 수집을 실행하지 않았습니다.", status="READY", http_status="", content_type="", auth_user=""):
        self.endpoint = endpoint
        self.last_scan = last_scan
        self.counts = counts or {}
        self.message = message
        self.status = status
        self.http_status = http_status
        self.content_type = content_type
        self.auth_user = auth_user

    def to_dict(self):
        return empty_dashboard({
            "endpoint": self.endpoint,
            "scan_endpoint": self.endpoint,
            "last_scan": self.last_scan,
            "counts": self.counts,
            "message": self.message,
            "status": self.status,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "auth_user": self.auth_user,
        })


class CatalogService(object):
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def last_summary(self):
        default = CatalogSummary(self._catalog_endpoint(), auth_user=self._username()).to_dict()
        saved = self.store.get_json("catalog_summary", default)
        return empty_dashboard(saved)

    def scan(self):
        endpoint = self._catalog_endpoint()
        username = self._username()
        started = time.time()
        status = "SUCCESS"
        message = ""
        http_status = ""
        content_type = ""
        items = []
        supported_types = []
        errors = []
        acl_summary = empty_acl_summary()
        try:
            parsed, meta = self._request_json(endpoint)
            http_status = meta.get("http_status", "")
            content_type = meta.get("content_type", "")
            supported_types, initial_items = split_catalog_response(parsed)
            items.extend(normalize_items(initial_items))

            if supported_types:
                for type_name in supported_types:
                    type_items = self._scan_type(type_name, errors)
                    items.extend(type_items)

            if not items and not supported_types:
                items = normalize_items(extract_catalog_items(parsed))

            items = dedupe_items(items)
            if items:
                acl_summary = self._enrich_acl(items, errors)

            dashboard = build_dashboard(
                endpoint=endpoint,
                scan_endpoint=endpoint,
                auth_user=username,
                last_scan=time.time(),
                status=status,
                http_status=http_status,
                content_type=content_type,
                message=message,
                items=items,
                supported_types=supported_types,
                errors=errors,
                acl_summary=acl_summary,
            )
            if errors and items:
                dashboard["status"] = "WARN"
                dashboard["message"] = "일부 type 또는 ACL 조회가 실패했습니다. 수집된 항목 기준으로 대시보드를 표시합니다."
            elif items:
                dashboard["message"] = "카탈로그 자산 대시보드 수집이 완료되었습니다."
            elif supported_types:
                dashboard["status"] = "WARN"
                dashboard["message"] = "지원 type 목록은 수집했지만 catalog item 목록은 수집하지 못했습니다. type endpoint 권한과 검색 조건을 확인하세요."
            else:
                dashboard["status"] = "WARN"
                dashboard["message"] = "JSON 응답은 받았지만 catalog item 또는 type 정보를 찾지 못했습니다."
        except urllib.error.HTTPError as exc:
            status = "FAILED"
            http_status = "HTTP {0}: {1}".format(exc.code, exc.reason)
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            if exc.code in (401, 403):
                message = "OAS REST 인증 또는 권한이 거부되었습니다. catalog_username/password를 확인하세요."
            else:
                message = "OAS REST 호출이 실패했습니다. endpoint, 인증, 권한을 확인하세요."
            dashboard = build_dashboard(endpoint, endpoint, username, time.time(), status, http_status, content_type, message, [], [], [], empty_acl_summary())
        except NonJsonResponse as exc:
            status = "FAILED"
            http_status = exc.http_status
            content_type = exc.content_type
            message = "REST API JSON 응답이 아닙니다. catalog_base_url/catalog_api_path 또는 인증 후 redirect 여부를 확인하세요."
            dashboard = build_dashboard(endpoint, endpoint, username, time.time(), status, http_status, content_type, message, [], [], [], empty_acl_summary())
        except Exception as exc:
            status = "FAILED"
            message = str(exc)
            dashboard = build_dashboard(endpoint, endpoint, username, time.time(), status, http_status, content_type, message, [], [], [], empty_acl_summary())

        self.store.set_json("catalog_summary", dashboard)
        self.store.add(Job(
            type="catalog_scan",
            command="GET {0}".format(endpoint),
            status=dashboard.get("status", status),
            exit_code=0 if dashboard.get("status") in ("SUCCESS", "WARN") else 1,
            started_at=started,
            ended_at=time.time(),
            message="{0} {1}".format(dashboard.get("http_status", ""), dashboard.get("message", "")).strip(),
        ))
        if dashboard.get("status") == "FAILED":
            raise RuntimeError(dashboard.get("message") or "catalog scan failed")
        return dashboard

    def _scan_type(self, type_name, errors):
        items = []
        for page in range(1, TYPE_PAGE_MAX + 1):
            url = self._catalog_type_endpoint(type_name, page)
            try:
                parsed, meta = self._request_json(url)
                page_items = normalize_items(extract_catalog_items(parsed), type_hint=type_name)
                items.extend(page_items)
                headers = meta.get("headers", {})
                next_page = header_value(headers, "oa-next-page")
                page_count = header_value(headers, "oa-page-count")
                if page_count:
                    try:
                        if page >= int(page_count):
                            break
                    except Exception:
                        pass
                if not next_page and len(page_items) < TYPE_PAGE_LIMIT:
                    break
            except Exception as exc:
                errors.append("{0}: {1}".format(type_name, exc))
                break
        return items

    def _enrich_acl(self, items, errors):
        summary = empty_acl_summary()
        candidates = [item for item in items if item.get("id") and item.get("type")]
        summary["eligible"] = len(candidates)
        for item in candidates[:ACL_CHECK_LIMIT]:
            summary["checked"] += 1
            try:
                acl = self._request_acl(item.get("type"), item.get("id"))
                risk = analyze_acl(acl)
                item["aclRisk"] = risk["level"]
                item["aclSummary"] = risk["summary"]
                item["aclPermissions"] = risk["permissions"]
                summary["risk_total"] += 1 if risk["level"] in ("WARN", "FAILED") else 0
                summary["broad_write"] += risk["broad_write"]
                summary["permission_management"] += risk["permission_management"]
            except Exception as exc:
                item["aclRisk"] = "WARN"
                item["aclSummary"] = "ACL 조회 실패"
                summary["acl_failed"] += 1
                summary["risk_total"] += 1
                errors.append("ACL {0}/{1}: {2}".format(item.get("type"), item.get("name"), exc))
        return summary

    def _request_acl(self, type_name, item_id):
        endpoint = self._catalog_action_endpoint(type_name, item_id, "getACL")
        parsed, _meta = self._request_json(endpoint, method="POST", data=b"")
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("items", "acl", "entries", "permissions"):
                if isinstance(parsed.get(key), list):
                    return parsed.get(key)
        return []

    def _request_json(self, endpoint, method="GET", data=None):
        headers = {"Accept": "application/json"}
        auth_header = self._auth_header()
        if auth_header:
            headers["Authorization"] = auth_header
        if method == "POST":
            headers["Content-Length"] = "0"
        req = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(5000000)
            code = getattr(resp, "status", None) or resp.getcode()
            reason = getattr(resp, "reason", "")
            http_status = "HTTP {0} {1}".format(code, reason).strip()
            content_type = resp.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                raise NonJsonResponse(http_status, content_type)
            parsed = json.loads(body.decode("utf-8", errors="replace"))
            return parsed, {"http_status": http_status, "content_type": content_type, "headers": resp.headers}

    def _catalog_endpoint(self):
        catalog_api_url = getattr(self.cfg.oas, "catalog_api_url", "") or ""
        if catalog_api_url.strip():
            return catalog_api_url.strip()
        base = (getattr(self.cfg.oas, "catalog_base_url", "") or "").strip()
        path = (getattr(self.cfg.oas, "catalog_api_path", "") or "").strip()
        if base and path:
            return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        base = self.cfg.oas.analytics_url.rstrip("/") + "/"
        return urljoin(base, "")

    def _catalog_type_endpoint(self, type_name, page):
        base = strip_query(self._catalog_endpoint()).rstrip("/")
        query = urlencode({"limit": TYPE_PAGE_LIMIT, "page": page})
        return "{0}/{1}?{2}".format(base, quote(str(type_name).strip(), safe=""), query)

    def _catalog_action_endpoint(self, type_name, item_id, action):
        base = strip_query(self._catalog_endpoint()).rstrip("/")
        return "{0}/{1}/{2}/actions/{3}".format(base, quote(str(type_name).strip(), safe=""), quote(str(item_id).strip(), safe=""), action)

    def _username(self):
        return (os.environ.get("OAS_ADMIN_LITE_CATALOG_USERNAME") or getattr(self.cfg.oas, "catalog_username", "") or "").strip()

    def _password(self):
        return os.environ.get("OAS_ADMIN_LITE_CATALOG_PASSWORD") or getattr(self.cfg.oas, "catalog_password", "") or ""

    def _auth_header(self):
        username = self._username()
        password = self._password()
        if not username:
            return ""
        token = base64.b64encode(("{0}:{1}".format(username, password)).encode("utf-8")).decode("ascii")
        return "Basic {0}".format(token)


class NonJsonResponse(Exception):
    def __init__(self, http_status, content_type):
        Exception.__init__(self, "non-json response")
        self.http_status = http_status
        self.content_type = content_type


def empty_acl_summary():
    return {"checked": 0, "eligible": 0, "risk_total": 0, "broad_write": 0, "permission_management": 0, "acl_failed": 0}


def empty_dashboard(data=None):
    result = {
        "endpoint": "",
        "scan_endpoint": "",
        "last_scan": 0.0,
        "counts": {},
        "type_rows": [],
        "owners": [],
        "folder_rows": [],
        "items": [],
        "supported_types": [],
        "total_assets": 0,
        "owner_count": 0,
        "modified_30_days": 0,
        "acl_summary": empty_acl_summary(),
        "message": "아직 수집을 실행하지 않았습니다.",
        "status": "READY",
        "http_status": "",
        "content_type": "",
        "auth_user": "",
        "errors": [],
        "limits": {"type_page_limit": TYPE_PAGE_LIMIT, "type_page_max": TYPE_PAGE_MAX, "acl_check_limit": ACL_CHECK_LIMIT, "detail_limit": DETAIL_LIMIT},
    }
    if data:
        result.update(data)
        result["counts"] = result.get("counts") or {}
        result["type_rows"] = result.get("type_rows") or []
        result["owners"] = result.get("owners") or []
        result["folder_rows"] = result.get("folder_rows") or []
        result["items"] = result.get("items") or []
        result["supported_types"] = result.get("supported_types") or []
        result["acl_summary"] = result.get("acl_summary") or empty_acl_summary()
    return result


def build_dashboard(endpoint, scan_endpoint, auth_user, last_scan, status, http_status, content_type, message, items, supported_types, errors, acl_summary):
    counts = count_by(items, "type")
    owner_counter = count_by_owner(items)
    folder_counter = count_by(items, "folder")
    now = datetime.now()
    modified_30_days = 0
    for item in items:
        modified = parse_datetime(item.get("lastModified", ""))
        if modified and modified >= now - timedelta(days=30):
            modified_30_days += 1
    type_rows = [{"type": key, "count": counts[key]} for key in sorted(counts, key=lambda item: counts[item], reverse=True)]
    owner_rows = build_owner_rows(items, owner_counter)
    folder_rows = [{"folder": key, "count": folder_counter[key]} for key in sorted(folder_counter, key=lambda item: folder_counter[item], reverse=True)[:TOP_LIMIT]]
    detail_items = sorted(items, key=lambda item: item.get("lastModified") or "", reverse=True)[:DETAIL_LIMIT]
    return empty_dashboard({
        "endpoint": endpoint,
        "scan_endpoint": scan_endpoint,
        "last_scan": last_scan,
        "counts": counts,
        "type_rows": type_rows,
        "owners": owner_rows,
        "folder_rows": folder_rows,
        "items": detail_items,
        "supported_types": supported_types,
        "total_assets": len(items),
        "owner_count": len(owner_counter),
        "modified_30_days": modified_30_days,
        "acl_summary": acl_summary,
        "message": message,
        "status": status,
        "http_status": http_status,
        "content_type": content_type,
        "auth_user": auth_user,
        "errors": errors[:20],
    })


def split_catalog_response(value):
    if isinstance(value, list):
        type_infos = []
        items = []
        for item in value:
            if is_type_info(item):
                type_infos.append(item.get("type"))
            elif isinstance(item, dict):
                items.append(item)
        if type_infos and not items:
            return type_infos, []
        return type_infos, items
    if isinstance(value, dict):
        candidates = []
        for key in ("items", "catalogItems", "data", "results"):
            if isinstance(value.get(key), list):
                candidates = value.get(key)
                break
        if candidates:
            return split_catalog_response(candidates)
    return [], []


def is_type_info(item):
    if not isinstance(item, dict):
        return False
    if set(item.keys()) == set(["type"]):
        return True
    has_item_field = any(item.get(key) for key in ID_KEYS + NAME_KEYS + OWNER_KEYS + LAST_MODIFIED_KEYS + PARENT_KEYS)
    return bool(item.get("type") and not has_item_field)


def extract_catalog_items(value):
    items = []
    visit_items(value, items)
    return items


def visit_items(value, items):
    if isinstance(value, dict):
        if not is_type_info(value) and looks_like_catalog_item(value):
            items.append(value)
            return
        for item in value.values():
            visit_items(item, items)
    elif isinstance(value, list):
        for item in value:
            visit_items(item, items)


def looks_like_catalog_item(value):
    return bool(first_value(value, TYPE_KEYS) and (first_value(value, ID_KEYS) or first_value(value, NAME_KEYS) or first_value(value, OWNER_KEYS) or first_value(value, LAST_MODIFIED_KEYS)))


def normalize_items(values, type_hint=""):
    result = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        item_type = str(first_value(value, TYPE_KEYS) or type_hint or "unknown")
        name = str(first_value(value, NAME_KEYS) or "-")
        item_id = str(first_value(value, ID_KEYS) or "")
        owner = str(first_value(value, OWNER_KEYS) or "unknown")
        last_modified = str(first_value(value, LAST_MODIFIED_KEYS) or "")
        parent_id = str(first_value(value, PARENT_KEYS) or "")
        path = str(first_value(value, PATH_KEYS) or decoded_catalog_path(item_id) or "")
        folder = folder_from_item(path, name, parent_id)
        result.append({
            "id": item_id,
            "name": name,
            "type": item_type,
            "owner": owner or "unknown",
            "lastModified": last_modified,
            "parentId": parent_id,
            "path": path,
            "folder": folder,
            "description": str(value.get("description", "") or ""),
            "aclRisk": "UNKNOWN",
            "aclSummary": "ACL 미조회",
            "aclPermissions": "",
        })
    return result


def first_value(data, keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def dedupe_items(items):
    seen = set()
    result = []
    for item in items:
        key = (item.get("type", ""), item.get("id", ""), item.get("path", ""), item.get("name", ""), item.get("owner", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def count_by(items, key):
    counts = {}
    for item in items:
        value = item.get(key) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def count_by_owner(items):
    counts = {}
    for item in items:
        owner = item.get("owner") or "unknown"
        counts[owner] = counts.get(owner, 0) + 1
    return counts


def build_owner_rows(items, owner_counter):
    rows = []
    for owner in sorted(owner_counter, key=lambda item: owner_counter[item], reverse=True)[:TOP_LIMIT]:
        owner_items = [item for item in items if (item.get("owner") or "unknown") == owner]
        folders = count_by(owner_items, "folder")
        last_modified = ""
        risk_count = 0
        for item in owner_items:
            if item.get("lastModified", "") > last_modified:
                last_modified = item.get("lastModified", "")
            if item.get("aclRisk") in ("WARN", "FAILED") or owner == "unknown":
                risk_count += 1
        top_folder = "-"
        if folders:
            top_folder = sorted(folders, key=lambda item: folders[item], reverse=True)[0]
        rows.append({"owner": owner, "count": owner_counter[owner], "lastModified": last_modified, "folder": top_folder, "risk": risk_count})
    return rows


def analyze_acl(acl):
    broad_write = 0
    permission_management = 0
    permissions_seen = []
    for entry in acl or []:
        if not isinstance(entry, dict):
            continue
        account = "{0} {1}".format(entry.get("accountGuid", ""), entry.get("accountDisplayName", "")).lower().replace(" ", "")
        permissions = entry.get("permissions") or {}
        enabled = [key for key, value in permissions.items() if value]
        permissions_seen.extend(enabled)
        has_risky = any(permissions.get(key) for key in RISK_PERMISSIONS)
        has_manage = any(permissions.get(key) for key in MANAGE_PERMISSIONS)
        if has_manage:
            permission_management += 1
        if has_risky and any(token.replace(" ", "") in account for token in BROAD_ACCOUNTS):
            broad_write += 1
    if broad_write:
        level = "FAILED"
        summary = "넓은 역할에 쓰기/삭제/권한관리 권한"
    elif permission_management:
        level = "WARN"
        summary = "권한 관리 권한 존재"
    else:
        level = "OK"
        summary = "고위험 ACL 없음"
    return {"level": level, "summary": summary, "broad_write": broad_write, "permission_management": permission_management, "permissions": ", ".join(sorted(set(permissions_seen)))[:120]}


def folder_from_item(path, name, parent_id):
    if path:
        normalized = path.replace("/@Catalog", "").strip()
        if normalized and "/" in normalized:
            folder = normalized.rsplit("/", 1)[0]
            return folder or "/"
        return normalized or "unknown"
    if parent_id:
        decoded = decoded_catalog_path(parent_id)
        if decoded:
            return decoded.replace("/@Catalog", "") or "/"
        return "parent:{0}".format(shorten(parent_id, 24))
    return "unknown"


def decoded_catalog_path(value):
    if not value:
        return ""
    text = str(value)
    if text.startswith("/"):
        return text
    try:
        padded = text + "=" * ((4 - len(text) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        if decoded.startswith("/"):
            return decoded
    except Exception:
        return ""
    return ""


def parse_datetime(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "")
    if "." in text:
        text = text.split(".", 1)[0]
    for fmt, size in (("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], fmt)
        except Exception:
            pass
    return None


def strip_query(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def header_value(headers, name):
    try:
        return headers.get(name) or headers.get(name.lower()) or ""
    except Exception:
        return ""


def shorten(value, limit):
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def infer_counts(value):
    counts = {}
    visit(value, counts)
    return counts


def visit(value, counts):
    if isinstance(value, dict):
        type_value = None
        for key in TYPE_KEYS:
            if isinstance(value.get(key), str):
                type_value = value.get(key)
                break
        if type_value:
            counts[type_value] = counts.get(type_value, 0) + 1
        for item in value.values():
            visit(item, counts)
    elif isinstance(value, list):
        for item in value:
            visit(item, counts)